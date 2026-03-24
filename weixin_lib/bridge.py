"""
微信-Claude Code 桥接服务。

将微信 iLink 消息转发给 Claude Code CLI，
根据配置过滤事件并将结果回传给微信用户。
"""

import json
import logging
import os
import time

from CC_lib.claude_cli import ClaudeChat
from weixin_lib.ilink_api import ILinkClient, extract_text
from weixin_lib.config import load_config, should_forward, get_prefix, get_max_length
from weixin_lib.scheduler import Scheduler

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
        # 用户 ID → context_token 映射（用于调度器回调发送消息）
        self._user_context = {}
        # 定时任务调度器
        self._scheduler = Scheduler(tasks_file=_TASKS_FILE)

    def login(self, force=False):
        """登录微信。如果已有有效 token 且非强制登录，直接复用。"""
        if not force and self._client.load_token():
            log.info(f"✅ 已加载微信 token（Bot: {self._client.account_id}）")
            log.info("   如需重新登录，运行: python run_weixin.py --login")
            return True
        return self._client.login()

    def run(self):
        """主循环：长轮询收消息 → Claude 处理 → 回传微信。"""
        self._scheduler.start(callback=self._on_task_due)
        log.info("🚀 桥接服务已启动（Ctrl+C 退出）...")

        retry_delay = 3  # 初始重试间隔
        max_delay = 60   # 最大重试间隔

        while True:
            try:
                msgs = self._client.get_updates()
                retry_delay = 3  # 成功后重置
                for msg in msgs:
                    if msg.get("message_type") != 1:
                        continue
                    self._handle_message(msg)
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

        # 发送 typing 状态
        self._client.send_typing(from_user, context_token)

        # 流式处理 Claude 响应
        log.info("   🤔 Claude 处理中...")
        pending_texts = []

        for event in self._chat.stream(text):
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
                break

        self._flush_pending(pending_texts, from_user, context_token)

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

    def _on_task_due(self, task):
        """调度器回调：定时任务到期，发送微信消息。"""
        target = task.get("target_user", "")
        message = task.get("message", "")
        if not target or not message:
            return
        context_token = self._user_context.get(target, "")
        task_type = task.get("type", "once")
        prefix = "⏰ 定时提醒：" if task_type == "once" else "⏰ 周期提醒："
        self._send_to_weixin(target, f"{prefix}{message}", context_token)

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
