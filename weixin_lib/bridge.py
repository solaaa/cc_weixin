"""
微信-Claude Code 桥接服务。

将微信 iLink 消息转发给 Claude Code CLI，
根据配置过滤事件并将结果回传给微信用户。
"""

import json
import logging
import os
import time
from datetime import datetime, date
from queue import Queue, Empty
from threading import Thread, Event

from CC_lib.claude_cli import ClaudeChat
from weixin_lib.ilink_api import ILinkClient, extract_text, extract_images, get_image_info, compress_image
from weixin_lib.config import load_config, should_forward, get_prefix, get_max_length
from weixin_lib.scheduler import Scheduler
from weixin_lib.chat_store import ChatStore

log = logging.getLogger("bridge")

# 项目根目录（run_weixin.py 所在目录）
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_DIR, "data")
_CURRENT_USER_FILE = os.path.join(_DATA_DIR, ".current_user")
_TASKS_FILE = os.path.join(_DATA_DIR, "scheduled_tasks.json")


class WeixinClaudeBridge:
    """微信-Claude 桥接服务。"""

    def __init__(self, config_path=None):
        self.config = load_config(config_path)
        self._client = ILinkClient(
            token_file=self.config.get("token_file", ".weixin-token.json"),
        )
        claude_cfg = self.config.get("claude", {})
        claude_cwd = claude_cfg.get("cwd") or _PROJECT_DIR
        self._chat = ClaudeChat(
            cwd=claude_cwd,
            permissions_path=claude_cfg.get("permissions_path"),
            effort=claude_cfg.get("effort"),
        )
        # 用户 ID → 是否在等待回答 AskUserQuestion
        self._waiting_answer = {}
        # 用户 ID → 是否在等待图片压缩确认
        self._waiting_image_confirm = {}
        # 用户 ID → context_token 映射（用于调度器回调发送消息）
        self._user_context = {}
        # 定时任务调度器
        self._scheduler = Scheduler(tasks_file=_TASKS_FILE)
        # 消息队列：统一调度用户消息和到期任务，避免并发冲突
        self._msg_queue = Queue()
        # 聊天记录存储
        self._chat_store = ChatStore()
        # 自动摘要配置
        self._summary_config = self.config.get("summary_schedule", {})
        self._last_summary_date = None
        self._summary_stop = Event()

    def login(self, force=False):
        """登录微信。如果已有有效 token 且非强制登录，直接复用。"""
        if not force and self._client.load_token():
            log.info(f"✅ 已加载微信 token（Bot: {self._client.account_id}）")
            log.info("   如需重新登录，运行: python run_weixin.py --login")
            return True
        return self._client.login()

    def run(self):
        """主循环：长轮询收消息 → 入队 → worker 线程处理。"""
        self._scheduler.start(callback=self._on_task_due)
        log.info("🚀 桥接服务已启动（Ctrl+C 退出）...")

        # 启动 worker 线程处理队列中的消息
        self._worker_thread = Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        # 启动自动摘要线程
        if self._summary_config.get("interval", "off") != "off":
            self._summary_thread = Thread(target=self._summary_loop, daemon=True)
            self._summary_thread.start()

        retry_delay = 3  # 初始重试间隔
        max_delay = 60   # 最大重试间隔

        while True:
            try:
                msgs = self._client.get_updates()
                retry_delay = 3  # 成功后重置
                for msg in msgs:
                    if msg.get("message_type") != 1:
                        continue
                    self._msg_queue.put(("user_msg", msg))
            except KeyboardInterrupt:
                log.info("👋 服务已停止")
                break
            except Exception as e:
                err_msg = str(e)
                if "session timeout" in err_msg.lower() or "-14" in err_msg:
                    log.error("❌ 微信 Session 已过期，请重新登录: python run_weixin.py --login")
                    break
                log.warning(f"⚠️  轮询出错: {err_msg}，{retry_delay}s 后重试...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    def _worker_loop(self):
        """Worker 线程：串行处理队列中的用户消息和到期任务。"""
        while True:
            try:
                item = self._msg_queue.get(timeout=1)
            except Empty:
                continue
            try:
                msg_type, payload = item
                if msg_type == "user_msg":
                    self._handle_message(payload)
                elif msg_type == "task_due":
                    self._handle_task_due(payload)
                elif msg_type == "auto_summary":
                    self._run_auto_summary()
            except Exception as e:
                log.error(f"Worker 处理出错: {e}", exc_info=True)

    def _handle_message(self, msg):
        """处理一条微信用户消息。"""
        from_user = msg["from_user_id"]
        text = extract_text(msg)
        context_token = msg.get("context_token", "")

        # 记录当前用户，供 schedule_cli.py 和调度器使用
        self._user_context[from_user] = context_token
        self._write_current_user(from_user)

        log.info(f"📩 [{time.strftime('%H:%M:%S')}] 收到消息")
        log.info(f"   From: {from_user}")
        log.info(f"   Text: {text}")

        # 如果这个用户正在等待 AskUserQuestion 的回答
        if self._waiting_answer.get(from_user):
            self._handle_ask_answer(from_user, text, context_token)
            return

        # 如果用户正在等待图片压缩确认
        if self._waiting_image_confirm.get(from_user):
            self._handle_image_confirm(from_user, text, context_token)
            return

        # 检测并下载图片
        image_items = extract_images(msg)
        downloaded_images = []
        if image_items:
            log.info(f"   📷 检测到 {len(image_items)} 张图片，正在下载...")
            for img_item in image_items:
                path, media_type = self._client.download_image(img_item)
                if path:
                    downloaded_images.append((path, media_type))

        # 如果有图片，检测尺寸判断是否需要压缩
        if downloaded_images:
            img_cfg = self.config.get("image", {})
            # 720p=1280, 1K=1920, 2K=2560, 2.7K=3440, 4K=3840
            max_long_edge = img_cfg.get("max_long_edge", 2560)
            needs_compress = False

            for img_path, _ in downloaded_images:
                info = get_image_info(img_path)
                if info:
                    w, h, fsize = info
                    long_edge = max(w, h)
                    if long_edge > max_long_edge:
                        needs_compress = True
                        # 计算压缩后的目标尺寸
                        ratio = max_long_edge / long_edge
                        target_w, target_h = int(w * ratio), int(h * ratio)
                        confirm_text = (
                            f"📷 图片尺寸较大 ({w}x{h}, {fsize/1024:.0f}KB)\n"
                            f"超过阈值 {max_long_edge}px，建议压缩到 {target_w}x{target_h}\n\n"
                            f"回复 1: 压缩后处理\n"
                            f"回复 2: 使用原图处理\n"
                            f"回复 3: 取消处理"
                        )
                        self._send_to_weixin(from_user, confirm_text, context_token)
                        self._waiting_image_confirm[from_user] = {
                            "context_token": context_token,
                            "images": downloaded_images,
                            "text": text,
                            "max_long_edge": max_long_edge,
                            "quality": img_cfg.get("compress_quality", 85),
                        }
                        log.info(f"   📷 图片 {w}x{h} 超过阈值 {max_long_edge}px，等待用户确认...")
                        return

        # 发送 typing 状态
        self._client.send_typing(from_user, context_token)

        # 发送消息给 Claude
        self._send_to_claude(from_user, text, context_token, downloaded_images)

    def _handle_image_confirm(self, from_user, text, context_token):
        """处理用户对图片压缩的确认回复。"""
        waiting = self._waiting_image_confirm.pop(from_user)
        images = waiting["images"]
        original_text = waiting["text"]
        choice = text.strip()

        if choice == "3":
            # 取消处理
            self._send_to_weixin(from_user, "已取消图片处理。", context_token)
            self._cleanup_images(images)
            return

        if choice == "1":
            # 压缩
            max_edge = waiting["max_long_edge"]
            quality = waiting["quality"]
            compressed_images = []
            for img_path, media_type in images:
                new_path = compress_image(img_path, max_edge, quality)
                if new_path and new_path != img_path:
                    compressed_images.append((new_path, "image/jpeg"))
                    # 删除原图
                    try:
                        os.remove(img_path)
                    except OSError:
                        pass
                else:
                    compressed_images.append((img_path, media_type))
            images = compressed_images

        # choice == "2" 或其他 → 使用原图

        self._client.send_typing(from_user, context_token)
        self._send_to_claude(from_user, original_text, context_token, images)

    def _send_to_claude(self, from_user, text, context_token, images):
        """将消息（可能含图片）发送给 Claude 并处理响应。"""
        # 如果有图片但没有文本，提供默认提示
        if images and text == "[图片]":
            text = "用户发送了图片，请分析这张图片的内容"

        # 流式处理 Claude 响应
        log.info("   🤔 Claude 处理中...")
        pending_texts = []
        result_text = ""

        for event in self._chat.stream(text, images=images if images else None):
            event_type = event.get("type", "")

            # AskUserQuestion: 发问题给微信用户，等待回答
            if event_type == "ask_user":
                question = event.get("question", "")
                options = event.get("options", [])
                ask_text = f"❓ AI 提问：{question}"
                if options:
                    ask_text += "\n" + "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(options))
                    ask_text += "\n\n请回复选项编号（如 1、2）或直接输入你的回答。"
                self._send_to_weixin(from_user, ask_text, context_token)
                self._waiting_answer[from_user] = {
                    "context_token": context_token,
                    "question": question,
                    "options": options,
                }
                # 先把已积累的消息发出
                self._flush_pending(pending_texts, from_user, context_token)
                log.info("   ❓ 等待用户回答 AskUserQuestion...")
                return

            # 提取可转发的文本
            forwarded = self._extract_forward_text(event)
            if forwarded:
                pending_texts.append(forwarded)

            # result 事件标志本轮结束
            if event_type == "result":
                result_text = event.get("result", "")
                break

        self._flush_pending(pending_texts, from_user, context_token)

        # 记录对话到历史
        self._record_conversation(text, result_text)

        # 清理临时图片文件
        self._cleanup_images(images)

    def _handle_ask_answer(self, from_user, text, context_token):
        """处理用户对 AskUserQuestion 的回答。"""
        log.info(f"   📝 收到 AskUserQuestion 回答: {text}")
        waiting = self._waiting_answer.pop(from_user)
        options = waiting.get("options", [])
        question = waiting.get("question", "")

        # 如果用户回复的是数字编号，转换为实际选项
        answer_text = text.strip()
        try:
            idx = int(answer_text) - 1
            if options and 0 <= idx < len(options):
                answer_text = options[idx]
        except ValueError:
            pass

        # 以明确格式发送，让 Claude 按用户选择继续
        formatted = f"关于你的提问「{question}」，用户选择了：{answer_text}"

        # 使用 answer 继续对话
        pending_texts = []
        for event in self._chat.answer(formatted):
            event_type = event.get("type", "")

            if event_type == "ask_user":
                q = event.get("question", "")
                opts = event.get("options", [])
                ask_text = f"❓ AI 提问：{q}"
                if opts:
                    ask_text += "\n" + "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(opts))
                    ask_text += "\n\n请回复选项编号（如 1、2）或直接输入你的回答。"
                self._send_to_weixin(from_user, ask_text, context_token)
                self._waiting_answer[from_user] = {
                    "context_token": context_token,
                    "question": q,
                    "options": opts,
                }
                self._flush_pending(pending_texts, from_user, context_token)
                return

            forwarded = self._extract_forward_text(event)
            if forwarded:
                pending_texts.append(forwarded)

            if event_type == "result":
                break

        self._flush_pending(pending_texts, from_user, context_token)

    def _record_conversation(self, user_text, agent_reply):
        """将有价值的对话记录到历史存储。跳过过短或无意义的消息。"""
        # 跳过过短的纯寒暄
        trivial = {"好", "好的", "嗯", "ok", "谢谢", "谢", "哦", "行", "收到", "嗯嗯", "对"}
        if user_text.strip().lower() in trivial:
            return
        try:
            if user_text.strip():
                self._chat_store.add_message("user", user_text)
            if agent_reply and agent_reply.strip():
                self._chat_store.add_message("assistant", agent_reply)
        except Exception as e:
            log.warning(f"记录对话失败: {e}")

    def _extract_forward_text(self, event):
        """从事件中提取需要转发的文本。返回 None 表示不转发。"""
        event_type = event.get("type", "")
        config = self.config

        if event_type == "system" and event.get("subtype") == "auto_compact":
            return event.get("message", "")

        if event_type == "assistant":
            parts = []
            for block in event.get("message", {}).get("content", []):
                block_type = block.get("type", "")

                if block_type == "thinking" and should_forward(config, "thinking"):
                    text = block.get("thinking", "")
                    prefix = get_prefix(config, "thinking")
                    parts.append(f"{prefix}{text}")

                elif block_type == "text" and should_forward(config, "text"):
                    text = block.get("text", "")
                    prefix = get_prefix(config, "text")
                    parts.append(f"{prefix}{text}")

                elif block_type == "tool_use" and should_forward(config, "tool_use"):
                    name = block.get("name", "?")
                    inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                    prefix = get_prefix(config, "tool_use")
                    parts.append(f"{prefix}[{name}] {inp}")

            return "\n".join(parts) if parts else None

        if event_type == "user" and should_forward(config, "tool_result"):
            parts = []
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    content = str(block.get("content", ""))
                    prefix = get_prefix(config, "tool_result")
                    parts.append(f"{prefix}{content}")
            return "\n".join(parts) if parts else None

        if event_type == "result" and should_forward(config, "result"):
            result = event.get("result", "")
            if result:
                prefix = get_prefix(config, "result")
                ctx = event.get("_context_tokens", 0)
                ctx_str = f"\n[上下文: {ctx/1000:.0f}k tokens]" if ctx else ""
                return f"{prefix}{result}{ctx_str}"

        return None

    def _flush_pending(self, pending_texts, to_user, context_token):
        """将积累的文本合并发送到微信。"""
        if not pending_texts:
            return

        full_text = "\n\n".join(pending_texts)
        max_len = get_max_length(self.config)

        # 分片发送
        chunks = _split_text(full_text, max_len)
        for chunk in chunks:
            self._send_to_weixin(to_user, chunk, context_token)

        pending_texts.clear()

    def _send_to_weixin(self, to_user, text, context_token):
        """发送文本到微信。"""
        try:
            self._client.send_text(to_user, text, context_token)
            preview = text[:60] + ("…" if len(text) > 60 else "")
            log.info(f"   ✅ 已发送: {preview}")
        except Exception as e:
            log.error(f"   ❌ 发送失败: {e}", exc_info=True)

    def _cleanup_images(self, images):
        """清理临时图片文件。"""
        for img_path, _ in images:
            try:
                os.remove(img_path)
            except OSError:
                pass

    def _on_task_due(self, task):
        """调度器回调：定时任务到期，入队处理。"""
        self._msg_queue.put(("task_due", task))

    def _handle_task_due(self, task):
        """处理到期任务：直接发送或交给 Agent 处理。"""
        target = task.get("target_user", "")
        message = task.get("message", "")
        if not target or not message:
            return
        context_token = self._user_context.get(target, "")
        task_type = task.get("type", "once")

        if task.get("agent_process"):
            # 交给 Agent 处理后再发送
            log.info(f"⏰ 到期任务 [{task.get('id')}] 交给 Agent 处理: {message[:50]}")
            prompt = f"[定时任务触发] 以下是用户预设的定时任务内容，请根据内容执行并回复用户：\n{message}"
            pending_texts = []
            for event in self._chat.stream(prompt):
                forwarded = self._extract_forward_text(event)
                if forwarded:
                    pending_texts.append(forwarded)
                if event.get("type") == "result":
                    break
            if pending_texts:
                prefix = "⏰ " if task_type == "once" else "⏰ "
                full_text = prefix + "\n\n".join(pending_texts)
            else:
                full_text = f"⏰ 定时任务已触发，但 Agent 未返回内容: {message}"
            self._flush_text(full_text, target, context_token)
        else:
            # 直接发送
            prefix = "⏰ 定时提醒：" if task_type == "once" else "⏰ 周期提醒："
            self._send_to_weixin(target, f"{prefix}{message}", context_token)

    def _flush_text(self, text, to_user, context_token):
        """将文本分片发送到微信。"""
        max_len = get_max_length(self.config)
        chunks = _split_text(text, max_len)
        for chunk in chunks:
            self._send_to_weixin(to_user, chunk, context_token)

    # ── 自动摘要 ──────────────────────────────────────────

    def _summary_loop(self):
        """后台线程：定时检查是否需要生成摘要。"""
        interval = self._summary_config.get("interval", "off")
        target_hour = self._summary_config.get("hour", 23)

        while not self._summary_stop.is_set():
            now = datetime.now()
            should_run = False

            if interval == "daily":
                # 每天在 target_hour 时执行一次
                if now.hour == target_hour and self._last_summary_date != now.date():
                    should_run = True
            elif interval == "weekly":
                # 每周一在 target_hour 时执行一次
                if now.weekday() == 0 and now.hour == target_hour and self._last_summary_date != now.date():
                    should_run = True

            if should_run:
                self._last_summary_date = now.date()
                self._msg_queue.put(("auto_summary", None))

            self._summary_stop.wait(3600)  # 每 60 分钟检查一次

    def _run_auto_summary(self):
        """执行自动摘要：获取未摘要的日期，让 Agent 为每个日期生成摘要。"""
        unsummarized = self._chat_store.get_unsummarized_dates()
        if not unsummarized:
            log.info("⏰ 自动摘要：所有消息已有摘要，跳过")
            return

        log.info(f"⏰ 自动摘要：发现 {len(unsummarized)} 个日期需要生成摘要")

        for date_info in unsummarized:
            date_str = date_info["date_str"]
            messages = self._chat_store.get_unsummarized_messages(date_str)
            if not messages:
                continue

            # 构造消息文本供 Agent 总结
            msg_lines = []
            for m in messages:
                role = "用户" if m["role"] == "user" else "Agent"
                msg_lines.append(f"{role}: {m['content'][:300]}")
            conversation_text = "\n".join(msg_lines)

            prompt = (
                f"[自动摘要任务] 请为以下 {date_str} 的对话生成一条简洁摘要（200字以内），"
                f"用分号分隔关键主题。只输出摘要文本，不要其他内容。\n\n{conversation_text}"
            )

            # 让 Agent 生成摘要
            summary_text = ""
            for event in self._chat.stream(prompt):
                if event.get("type") == "result":
                    summary_text = event.get("result", "").strip()
                    break

            if summary_text:
                message_ids = [m["id"] for m in messages]
                self._chat_store.create_summary(date_str, summary_text, message_ids)
                log.info(f"⏰ 自动摘要：{date_str} 已生成摘要（{len(message_ids)} 条消息）")
            else:
                log.warning(f"⏰ 自动摘要：{date_str} Agent 未返回摘要内容")

    @staticmethod
    def _write_current_user(user_id):
        """写入当前用户 ID，供 schedule_cli.py 读取。"""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_CURRENT_USER_FILE, "w", encoding="utf-8") as f:
                json.dump({"user_id": user_id}, f)
        except OSError:
            pass

    def stop(self):
        """停止服务。"""
        self._summary_stop.set()
        self._scheduler.stop()
        self._chat.stop()


def _split_text(text, max_len):
    """将长文本按最大长度分片，尽量在换行符处断开。"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 在 max_len 范围内找最后一个换行
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
