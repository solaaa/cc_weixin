# 微信-Claude Code 桥接服务 — 项目代码细节手册

> 面向新接手开发人员的项目技术文档，涵盖架构、运作原理和各模块实现细节。

---

## 目录

1. [项目总览](#1-项目总览)
2. [目录结构](#2-目录结构)
3. [整体架构](#3-整体架构)
4. [启动流程](#4-启动流程)
5. [核心模块详解](#5-核心模块详解)
   - 5.1 [bridge.py — 桥接服务核心](#51-bridgepy--桥接服务核心)
   - 5.2 [claude_cli.py — Claude Code CLI 管理器](#52-claude_clipy--claude-code-cli-管理器)
   - 5.3 [ilink_api.py — 微信 iLink API 客户端](#53-ilink_apipy--微信-ilink-api-客户端)
   - 5.4 [config.py — 配置管理](#54-configpy--配置管理)
   - 5.5 [chat_store.py — 聊天记录持久化](#55-chat_storepy--聊天记录持久化)
   - 5.6 [scheduler.py — 定时任务调度器](#56-schedulerpy--定时任务调度器)
   - 5.7 [schedule_cli.py — 定时任务 CLI](#57-schedule_clipy--定时任务-cli)
   - 5.8 [chat_history_cli.py — 聊天历史 CLI](#58-chat_history_clipy--聊天历史-cli)
   - 5.9 [logger.py — 日志系统](#59-loggerpy--日志系统)
6. [消息处理完整流程](#6-消息处理完整流程)
7. [图片处理流程](#7-图片处理流程)
8. [事件转发机制](#8-事件转发机制)
9. [Claude Code stream-json 协议](#9-claude-code-stream-json-协议)
10. [定时任务系统](#10-定时任务系统)
11. [聊天历史与自动摘要](#11-聊天历史与自动摘要)
12. [斜杠命令系统](#12-斜杠命令系统)
13. [配置文件说明](#13-配置文件说明)
14. [Skill 系统](#14-skill-系统)
15. [辅助入口脚本](#15-辅助入口脚本)

---

## 1. 项目总览

本项目是一个 **微信-Claude Code 桥接服务**，将微信用户消息转发给 Claude Code CLI 处理，再将处理结果回传给微信。

核心能力：
- 微信消息接收与回复（基于 iLink Bot API）
- Claude Code CLI 持久化会话管理（stream-json 双向协议）
- 图片消息处理（下载、解密、压缩、多模态发送）
- 定时任务/提醒系统
- 聊天历史持久化与自动摘要
- 工具调用事件的可配置转发
- AskUserQuestion 交互式回调

运行环境：Windows（Python 3.x），Claude Code CLI 需预装。

---

## 2. 目录结构

```
cc_weixin/
├── run_weixin.py              # 主入口：微信桥接服务
├── run_chat.py                # 辅助入口：纯终端对话（调试用）
├── start.bat                  # Windows 一键启动脚本
├── wechat-claude-bridge.mjs   # Node.js 版本 demo（早期/备选）
├── CLAUDE.md                  # Claude Code 的系统指令文件
├── CODE_SYS_PROMPT.md         # 代码编写的系统提示规则
│
├── CC_lib/                    # Claude Code CLI 封装层
│   ├── claude_cli.py          # 核心：持久化进程管理 + stream-json 协议
│   └── permissions.json       # 工具权限配置
│
├── weixin_lib/                # 微信桥接库
│   ├── __init__.py            # 包入口，导出 WeixinClaudeBridge 等
│   ├── bridge.py              # 核心：消息调度、事件转发、图片处理
│   ├── ilink_api.py           # 微信 iLink Bot API HTTP 客户端
│   ├── config.py              # 配置加载与合并
│   ├── default_config.json    # 默认配置
│   ├── chat_store.py          # SQLite 聊天记录存储
│   ├── chat_history_cli.py    # 聊天历史 CLI（供 Claude 调用）
│   ├── scheduler.py           # 定时任务调度器引擎
│   ├── schedule_cli.py        # 定时任务管理 CLI（供 Claude 调用）
│   └── logger.py              # 日志配置
│
├── config/                    # 扩展配置
│   └── mcporter.json          # MCP 工具配置
│
├── data/                      # 运行时数据（自动生成）
│   ├── scheduled_tasks.json   # 定时任务持久化
│   ├── chat_history.db        # SQLite 聊天记录数据库
│   └── .current_user          # 当前活跃用户 ID（供 CLI 工具读取）
│
├── LOG/                       # 日志目录
│   ├── info.log               # 全量日志（INFO+）
│   └── error.log              # 错误日志（WARNING+）
│
└── .claude/                   # Claude Code 配置
    └── skills/                # 自定义 Skill 定义
        ├── chat-history/SKILL.md
        └── skill-creator/SKILL.md
```

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户（微信）                              │
└─────────────┬───────────────────────────────┬───────────────────┘
              │ 发送消息                       ▲ 接收回复
              ▼                               │
┌─────────────────────────────────────────────────────────────────┐
│               微信 iLink Bot API（云端）                         │
│          https://ilinkai.weixin.qq.com                          │
└─────────────┬───────────────────────────────┬───────────────────┘
              │ HTTP 长轮询 (getupdates)       ▲ HTTP POST (sendmessage)
              ▼                               │
┌─────────────────────────────────────────────────────────────────┐
│                    ilink_api.py                                  │
│            ILinkClient: 登录/轮询/发送/图片下载                    │
└─────────────┬───────────────────────────────┬───────────────────┘
              │                               ▲
              ▼                               │
┌─────────────────────────────────────────────────────────────────┐
│                      bridge.py                                   │
│  WeixinClaudeBridge: 消息调度、事件过滤、图片处理、AskUser 交互     │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │  Scheduler   │ │  ChatStore   │ │  _extract_forward_text   │ │
│  │  定时任务     │ │  聊天记录     │ │  事件过滤/格式化          │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘ │
└─────────────┬───────────────────────────────┬───────────────────┘
              │ stdin (JSON lines)             ▲ stdout (JSON events)
              ▼                               │
┌─────────────────────────────────────────────────────────────────┐
│                    claude_cli.py                                  │
│  ClaudeChat: 持久化子进程管理 + stream-json 双向协议               │
└─────────────┬───────────────────────────────────────────────────┘
              │ subprocess
              ▼
┌─────────────────────────────────────────────────────────────────┐
│             Claude Code CLI (claude -p ...)                       │
│  --input-format stream-json --output-format stream-json          │
└─────────────────────────────────────────────────────────────────┘
```

**数据流向**：微信 → iLink API → Bridge → Claude CLI → Claude 模型 → Claude CLI → Bridge → iLink API → 微信

---

## 4. 启动流程

### 4.1 主入口 `run_weixin.py`

```
python run_weixin.py [--login] [--config 配置文件路径]
```

执行流程：
1. `setup_logger()` — 初始化日志系统（LOG 目录、info.log、error.log、终端输出）
2. `WeixinClaudeBridge(config_path)` — 实例化桥接服务（加载配置、创建 ILinkClient、ClaudeChat、Scheduler、ChatStore）
3. `bridge.login(force)` — 微信登录（有效 token 直接复用，否则扫码）
4. `bridge.run()` — 进入主循环

### 4.2 `bridge.run()` 内部

启动以下线程和循环：
- **主线程**：`while True` 长轮询 `ILinkClient.get_updates()`，收到消息放入 `_msg_queue`
- **Worker 线程**（`_worker_loop`）：串行消费 `_msg_queue`，处理三类任务：
  - `user_msg` — 用户微信消息
  - `task_due` — 定时任务到期
  - `auto_summary` — 自动摘要触发
- **Scheduler 线程**（`scheduler._loop`）：每 30s 检查 `scheduled_tasks.json`，到期任务入队
- **Summary 线程**（`_summary_loop`）：每 60 分钟检查，在配置的整点触发自动摘要

**关键设计**：所有消息处理都在 Worker 线程中串行执行，避免并发冲突（Claude CLI 是单进程单会话）。

---

## 5. 核心模块详解

### 5.1 `bridge.py` — 桥接服务核心

**类**: `WeixinClaudeBridge`

**核心职责**：
- 接收微信消息并分发处理
- 管理 AskUserQuestion 和图片压缩确认等交互状态
- 过滤和格式化 Claude 事件后转发到微信
- 协调定时任务和自动摘要

**关键属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `_client` | `ILinkClient` | 微信 API 客户端 |
| `_chat` | `ClaudeChat` | Claude CLI 管理器 |
| `_waiting_answer` | `dict` | 用户 ID → 是否在等待 AskUserQuestion 回答 |
| `_waiting_image_confirm` | `dict` | 用户 ID → 图片压缩确认等待状态 |
| `_user_context` | `dict` | 用户 ID → context_token 映射 |
| `_scheduler` | `Scheduler` | 定时任务调度器 |
| `_msg_queue` | `Queue` | 统一消息队列 |
| `_chat_store` | `ChatStore` | 聊天记录存储 |

**关键方法**：

- `_handle_message(msg)` — 消息入口。判断是否在等待回答/图片确认，处理图片下载，发送给 Claude。
- `_send_to_claude(from_user, text, context_token, images)` — 流式调用 Claude，逐事件提取转发文本，处理 AskUserQuestion。
- `_extract_forward_text(event)` — 根据配置决定哪些事件类型转发到微信，格式化文本。
- `_handle_ask_answer(from_user, text, context_token)` — 将用户的回答转换为格式化文本发回 Claude 继续对话。
- `_handle_image_confirm(from_user, text, context_token)` — 处理用户对图片压缩的选择（1 压缩/2 原图/3 取消）。
- `_record_conversation(user_text, agent_reply)` — 将非寒暄对话写入 SQLite 历史。
- `_handle_task_due(task)` — 定时任务到期处理：直接发送或交给 Agent 处理。
- `_flush_pending(pending_texts, to_user, context_token)` — 合并多条文本后分片发送。
- `_split_text(text, max_len)` — 长文本在换行符处智能分片。

### 5.2 `claude_cli.py` — Claude Code CLI 管理器

**类**: `ClaudeChat`

**核心职责**：
- 管理一个持久化的 Claude Code 子进程
- 实现 stream-json 双向通信协议
- 处理斜杠命令（本地拦截或转换）
- 检测和分发 AskUserQuestion 事件
- 上下文 token 跟踪和自动压缩

**进程启动命令**：
```
claude -p --input-format stream-json --output-format stream-json --verbose
       [--dangerously-skip-permissions]
       [--disallowedTools Tool1 Tool2 ...]
       [--model <model_name>]
       [--effort <low|medium|high|max>]
```

**通信架构**：
```
主线程                       后台 _read_loop 线程
   │                              │
   │ _write(json) → stdin -----→ Claude CLI
   │                              │
   │                Claude CLI → stdout -----→ _read_loop
   │                              │ json.loads(line)
   │                              │ event_queue.put(event)
   │                              │
   │ ← event_queue.get() --------│
   │ yield event
```

**关键方法**：

- `start()` — 启动子进程 + 启动 `_read_loop` 后台读线程
- `stop()` — 关闭 stdin → 等待进程退出
- `_read_loop()` — 后台线程，持续读 stdout，每行 JSON 解析后放入 `_event_queue`
- `_write(msg_dict)` — JSON 序列化后写入 stdin（换行结尾）
- `_send_user_message(text)` — 发送纯文本用户消息
- `_send_multimodal_message(text, images)` — 发送包含 base64 图片的多模态消息
- `stream(message, _raw, images)` — 核心方法：发送消息 → yield 所有事件直到 result
- `answer(text)` — 回答 AskUserQuestion（实际是以新用户消息形式继续对话）
- `send(message)` — 简单模式：发送并只返回最终文本

**斜杠命令系统**：

| 命令 | 类型 | 处理方式 |
|------|------|---------|
| `/clear` | local | 重启进程，清空上下文 |
| `/compact` | local | 让 Claude 总结 → 重启 → 注入摘要恢复上下文 |
| `/cost` | local | 显示累计费用和轮数 |
| `/status` | local | 显示进程状态、会话 ID、模型、费用 |
| `/model xxx` | local | 切换模型（重启进程后生效） |
| `/help` | local | 列出所有命令 |
| `/permissions` | local | 显示工具权限 |
| `/init` | prompt | 转为 "创建 CLAUDE.md" 提示词发送 |
| `/review` | prompt | 转为 "代码审查" 提示词发送 |
| `/memory` | prompt | 转为 "显示 CLAUDE.md" 提示词发送 |
| 其他 /xxx | — | 原样发给 Claude（可能是自定义 Skill） |

**自动压缩机制**：
1. 每次收到 assistant 事件，记录 `input_tokens + cache_read_input_tokens`
2. 在 result 事件中检查是否超过阈值（默认 90000 token）
3. 如果超过，标记 `_needs_compact = True`
4. 下一次 `stream()` 调用开始时执行压缩：
   - yield 一个 system/auto_compact 事件（通知微信用户）
   - 调用 `_do_auto_compact()`：让 Claude 总结 → 重启 → 注入摘要

### 5.3 `ilink_api.py` — 微信 iLink API 客户端

**类**: `ILinkClient`

**核心职责**：
- 扫码登录（QR 码渲染 + 轮询状态）
- Token 持久化（保存到 `.weixin-token.json`）
- 长轮询收消息（`get_updates`，最长阻塞 38 秒）
- 发送文本消息（`send_text`）
- 发送"正在输入"状态（`send_typing`）
- 图片下载与解密

**HTTP 请求格式**：
- 所有请求都加 `AuthorizationType: ilink_bot_token` 头
- `X-WECHAT-UIN` 头：随机 uint32 的 base64 编码
- Body 中自动注入 `base_info.channel_version`
- 认证：`Authorization: Bearer {token}`

**图片下载流程**：
1. 从消息中提取 `media.encrypt_query_param`
2. 从 CDN 下载：`https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=...`
3. AES-128-ECB 解密（key 来自 `image_item.aeskey` 或 `media.aes_key`）
4. 魔数检测推断文件格式（PNG/JPEG/GIF/WEBP）
5. 保存到系统临时目录（`wx_img_xxxxxxxx.ext`）

**消息解析工具函数**：
- `extract_text(msg)` — 从 `item_list` 提取文本（文本/语音转文字/文件名/图片占位符）
- `extract_images(msg)` — 提取图片项列表
- `get_image_info(file_path)` — 用 PIL 获取宽高和文件大小
- `compress_image(file_path, max_long_edge, quality)` — 等比缩放 + JPEG 压缩

### 5.4 `config.py` — 配置管理

**功能**：加载默认配置 → 查找用户配置 → 深度合并。

**配置查找顺序**：
1. 启动参数 `--config` 指定的路径
2. 项目根目录 `weixin_config.json`
3. 仅使用 `weixin_lib/default_config.json`

**深度合并规则**：用户配置中的值覆盖默认值；嵌套 dict 递归合并；以 `_` 开头的 key 跳过（注释字段）。

**工具函数**：
- `should_forward(config, event_type)` — 判断事件是否应转发（result 强制 true）
- `get_prefix(config, event_type)` — 获取事件前缀字符串
- `get_max_length(config)` — 获取单条消息最大长度（默认 2000）

### 5.5 `chat_store.py` — 聊天记录持久化

**类**: `ChatStore`

**存储**：SQLite 数据库 `data/chat_history.db`

**表结构**：

```
messages 表：
  id          INTEGER PRIMARY KEY  — 自增 ID
  timestamp   TEXT                 — ISO 时间戳
  date_str    TEXT                 — 日期字符串 (YYYY-MM-DD)
  role        TEXT                 — "user" 或 "assistant"
  content     TEXT                 — 消息文本
  session_id  TEXT                 — 会话 ID（可选）
  summary_id  INTEGER              — 关联的摘要 ID（NULL=未摘要）

summaries 表：
  id          INTEGER PRIMARY KEY  — 自增 ID
  date_str    TEXT                 — 日期字符串
  summary     TEXT                 — 摘要文本
  created_at  TEXT                 — 创建时间
```

**两级检索设计**：
1. 第 1 级：搜索摘要（`search_summaries`）— 按关键词/日期范围模糊匹配
2. 第 2 级：查看原始消息（`get_messages_by_summary`）— 根据摘要 ID 获取关联消息

**注意**：只保存文本，不保存图片 base64 数据。寒暄消息（"好"、"嗯"、"ok"等）不记录。

### 5.6 `scheduler.py` — 定时任务调度器

**类**: `Scheduler`

**运行方式**：后台线程，每 30 秒检查一次 `scheduled_tasks.json`。

**任务类型**：
- `once` — 一次性任务：比较 `trigger_time` 与当前时间，到期则触发并标记 `status=done`
- `cron` — 周期任务：解析 cron 表达式，计算上次运行后的下一次触发时间，到期则触发并更新 `last_run`

**cron 表达式**：5 字段格式（分 时 日 月 周几），支持 `*`、逗号、范围（`1-5`）、步进（`*/5`）。

**注意**：weekday 使用 Python 的 `datetime.weekday()` 约定：0=周一 ... 6=周日。

**任务到期后**：调用 `callback(task)` → Bridge 将任务入队 → Worker 线程处理。

### 5.7 `schedule_cli.py` — 定时任务 CLI

供 Claude Code 通过 Bash 工具调用的命令行接口。

**命令**：
```
python weixin_lib/schedule_cli.py add --time "15:00" --message "内容" [--agent]
python weixin_lib/schedule_cli.py add --cron "0 9 * * 1-5" --message "内容" [--agent]
python weixin_lib/schedule_cli.py list
python weixin_lib/schedule_cli.py delete --id <ID>
```

**目标用户**：从 `data/.current_user` 文件读取，由 Bridge 在收到消息时自动写入。

### 5.8 `chat_history_cli.py` — 聊天历史 CLI

供 Claude Code 通过 Bash 工具调用的聊天历史检索接口。

**命令**：
```
search-summaries --query "关键词" [--from 日期] [--to 日期]
get-messages --summary-id <ID>
dates [--limit N]
unsummarized
messages-by-date --date YYYY-MM-DD
create-summary --date YYYY-MM-DD --text "摘要文本"
```

### 5.9 `logger.py` — 日志系统

**输出目标**：
- `LOG/info.log` — INFO 及以上（全量记录）
- `LOG/error.log` — WARNING 及以上（仅错误）
- 终端 — INFO 及以上（实时可见）

**日志格式**：`2025-03-26 14:30:00 [INFO] 消息内容`

---

## 6. 消息处理完整流程

以用户发送一条普通文本消息为例：

```
1. 微信用户发送消息
     ↓
2. ILinkClient.get_updates() 长轮询返回消息
     ↓
3. 主线程将消息入队 _msg_queue.put(("user_msg", msg))
     ↓
4. Worker 线程取出消息 → _handle_message(msg)
     ↓
5. 提取 from_user、text、context_token
   记录当前用户到 data/.current_user
     ↓
6. 检查状态：
   - 在等待 AskUserQuestion 回答？ → _handle_ask_answer()
   - 在等待图片压缩确认？ → _handle_image_confirm()
   - 有图片？ → 下载图片，检测尺寸，可能询问是否压缩
     ↓
7. 发送 typing 状态（"正在输入"）
     ↓
8. _send_to_claude(from_user, text, context_token, images)
     ↓
9. ClaudeChat.stream(text, images) — 发送消息到 Claude CLI
     ↓
10. 逐个接收事件：
    - ask_user → 发到微信，暂停处理，设置等待状态
    - assistant/thinking → 按配置决定是否转发
    - assistant/text → 按配置决定是否转发
    - assistant/tool_use → 按配置决定是否转发（简化格式）
    - user/tool_result → 按配置决定是否转发
    - result → 本轮结束
     ↓
11. _flush_pending() — 合并所有待发文本，分片发送到微信
     ↓
12. _record_conversation() — 写入聊天历史 SQLite
     ↓
13. _cleanup_images() — 删除临时图片文件
```

---

## 7. 图片处理流程

```
1. extract_images(msg) 从消息中提取图片 item
     ↓
2. ILinkClient.download_image(image_item)
   - CDN 下载：https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=...
   - AES-128-ECB 解密（key = image_item.aeskey 或 media.aes_key）
   - 魔数检测格式 → 保存到 %TEMP%/wx_img_xxxxxxxx.ext
     ↓
3. get_image_info(file_path) — 获取宽高和文件大小
     ↓
4. 尺寸检测：长边是否超过阈值（默认 2560px）？
   - 超过 → 发送压缩确认到微信，等待用户选择（1/2/3）
   - 未超过 → 直接使用原图
     ↓
5. ClaudeChat._send_multimodal_message(text, images)
   - 读取图片文件 → base64 编码
   - 构造 content 数组：[{type:image, source:{type:base64,...}}, {type:text,...}]
   - JSON 写入 Claude CLI stdin
     ↓
6. 处理完毕后 _cleanup_images() 删除临时文件
```

**图片上下文留存**：base64 数据作为对话历史的一部分存在于 Claude CLI 进程的上下文中。在进程存活期间，用户可以继续对图片提问。但图片不会保存到聊天历史数据库。

---

## 8. 事件转发机制

**配置**（`default_config.json`）：

```json
{
  "forward_events": {
    "thinking": false,    // 思考过程
    "text": false,        // 文本输出
    "tool_use": true,     // 工具调用
    "tool_result": false, // 工具结果
    "result": true        // 最终结果（强制 true）
  },
  "message_prefix": {
    "thinking": "💭 思考中：",
    "text": "",
    "tool_use": "🔧 调用工具：",
    "tool_result": "📋 工具结果：",
    "result": ""
  }
}
```

**_extract_forward_text()** 根据事件类型：

| 事件类型 | 条件 | 转发格式 |
|---------|------|---------|
| `system/auto_compact` | 总是 | 原始消息文本 |
| `assistant/thinking` | `forward_events.thinking=true` | `💭 思考中：<内容>` |
| `assistant/text` | `forward_events.text=true` | `<内容>` |
| `assistant/tool_use` | `forward_events.tool_use=true` | `🔧 调用工具：<工具名>` |
| `assistant/tool_use` (Skill) | `forward_events.tool_use=true` | `🔧 调用工具：[Skill] - <skill名>` |
| `user/tool_result` | `forward_events.tool_result=true` | `📋 工具结果：<内容>` |
| `result` | 总是 | `<结果>[上下文: Nk tokens]` |

**注意**：tool_use 转发到微信时只显示工具名称（不含完整 JSON input），本地日志（`format_event`）仍保留完整信息。

---

## 9. Claude Code stream-json 协议

### 9.1 输入协议（stdin → Claude CLI）

每行一个 JSON 对象：

**普通文本消息**：
```json
{"type": "user", "message": {"role": "user", "content": "用户文本"}}
```

**多模态消息（含图片）**：
```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "<base64>"}},
      {"type": "text", "text": "请描述这张图片"}
    ]
  }
}
```

### 9.2 输出协议（Claude CLI → stdout）

事件流，每行一个 JSON 对象：

**系统初始化**：
```json
{"type": "system", "subtype": "init", "session_id": "...", "tools": [...]}
```

**助手消息**（多种 content block）：
```json
{
  "type": "assistant",
  "message": {
    "content": [
      {"type": "thinking", "thinking": "我需要..."},
      {"type": "text", "text": "回复内容"},
      {"type": "tool_use", "id": "toolu_xxx", "name": "Bash", "input": {"command": "ls"}}
    ],
    "usage": {"input_tokens": 1500, "cache_read_input_tokens": 500}
  }
}
```

**工具结果**（Claude 自动执行工具后）：
```json
{
  "type": "user",
  "message": {
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "执行结果"}
    ]
  }
}
```

**本轮结束**：
```json
{
  "type": "result",
  "result": "最终回复文本",
  "total_cost_usd": 0.0123,
  "num_turns": 3,
  "duration_ms": 5000,
  "modelUsage": {"claude-sonnet-4-20250514": {"contextWindow": 200000}}
}
```

**特殊事件 — AskUserQuestion**：
当 `tool_use.name == "AskUserQuestion"` 时，表示 Claude 需要向用户提问：
```json
{
  "type": "tool_use",
  "name": "AskUserQuestion",
  "input": {
    "question": "你想用哪个方案？",
    "options": ["方案A", "方案B"]
  }
}
```
新版格式使用 `input.questions[0].question` + `options[{label, description}]`。

---

## 10. 定时任务系统

### 10.1 架构

```
schedule_cli.py (用户/Claude调用) → scheduled_tasks.json (持久化)
                                            ↑
                                      Scheduler (后台线程, 30s 检查)
                                            ↓
                                    Bridge._on_task_due() → _msg_queue
                                            ↓
                                    Worker 线程: _handle_task_due()
                                        ├── 直接发送模式 → 发微信
                                        └── Agent 处理模式 → Claude处理 → 发微信
```

### 10.2 任务 JSON 格式

```json
{
  "id": "a1b2c3d4",
  "type": "once",
  "message": "开会提醒",
  "target_user": "user_id_xxx",
  "created_at": "2025-03-26T10:00:00",
  "trigger_time": "2025-03-26T15:00:00",
  "status": "pending",
  "agent_process": false
}
```

cron 类型额外字段：`cron_expr`、`last_run`。

### 10.3 时间解析

`_parse_time_str()` 支持：
- `"15:00"` — 今天的 15:00，如果已过则明天
- `"2025-05-03 15:00"` — 指定日期时间
- 多种日期格式：`YYYY-MM-DD HH:MM`、`YYYY/MM/DD HH:MM`

---

## 11. 聊天历史与自动摘要

### 11.1 记录规则

`_record_conversation()` 在每轮对话结束后调用：
- 跳过寒暄消息："好"、"嗯"、"ok"、"谢谢" 等
- 只保存文本，不保存图片
- 分别保存 user 和 assistant 的消息

### 11.2 自动摘要

**配置**（`default_config.json`）：
```json
{
  "summary_schedule": {
    "interval": "daily",  // off / daily / weekly
    "hour": 4             // 每天凌晨 4 点执行
  }
}
```

**流程**：
1. 后台线程每 60 分钟检查一次
2. 到达配置的小时且当天未执行 → 获取所有未关联摘要的日期
3. 对每个日期：提取消息 → 构造总结 prompt → 让 Claude 生成摘要（200 字以内）
4. 将摘要写入 `summaries` 表，关联对应的消息 ID

### 11.3 检索方式

Claude 通过 `chat_history_cli.py` 检索历史：
1. `search-summaries --query "关键词"` — 搜摘要（看目录）
2. `get-messages --summary-id N` — 看原始消息（看内容）
3. `messages-by-date --date YYYY-MM-DD` — 直接按日期查原始消息

---

## 12. 斜杠命令系统

用户在微信中发送 `/xxx` 格式的消息，由 `claude_cli.py` 拦截处理。

**三种处理类型**：
1. **local** — 完全在本地处理，不发给 Claude（如 /clear、/cost）
2. **prompt** — 转换为等效提示词发给 Claude（如 /init → "创建 CLAUDE.md"）
3. **unsupported** — 在 stream-json 模式下不可用（如 /login、/doctor）

**自定义命令**：不匹配任何内置命令的 `/xxx` 会原样发给 Claude，可能触发自定义 Skill。

---

## 13. 配置文件说明

### 13.1 weixin_lib/default_config.json

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `forward_events.thinking` | false | 是否转发思考过程 |
| `forward_events.text` | false | 是否转发中间文本 |
| `forward_events.tool_use` | true | 是否转发工具调用 |
| `forward_events.tool_result` | false | 是否转发工具结果 |
| `forward_events.result` | true | 是否转发最终结果（强制 true） |
| `message_prefix.*` | 各种前缀 | 各事件类型的微信消息前缀 |
| `max_message_length` | 2000 | 单条微信消息最大字符数 |
| `image.max_long_edge` | 2560 | 图片长边阈值（px），超过询问压缩 |
| `image.compress_quality` | 85 | JPEG 压缩质量 |
| `claude.cwd` | null(=项目目录) | Claude CLI 工作目录 |
| `claude.permissions_path` | null | 权限配置文件路径 |
| `claude.effort` | "medium" | Claude 思考深度 |
| `summary_schedule.interval` | "daily" | 摘要频率：off/daily/weekly |
| `summary_schedule.hour` | 4 | 摘要执行小时 |
| `token_file` | ".weixin-token.json" | 微信 token 文件名 |

### 13.2 CC_lib/permissions.json

控制 Claude Code 的工具权限：
- `skip_all_permissions: true` — 跳过所有确认弹窗
- `auto_compact.enabled: true` — 启用自动上下文压缩
- `auto_compact.threshold_tokens: 90000` — 压缩阈值
- `tools.*` — 各工具是否允许（true/false）

允许的工具：Task、Bash、Read、Edit、Write、WebFetch、WebSearch、Skill、AskUserQuestion 等。

---

## 14. Skill 系统

**Skills** 是 Claude Code 的自定义能力扩展，定义在 `.claude/skills/` 目录下。

当前项目定义了两个 Skill：
- `chat-history` — 搜索和检索过去的对话历史
- `skill-creator` — 创建和管理 Skill

Claude Code 通过 `Skill` 工具调用加载 Skill。在 tool_use 事件中，`name="Skill"`，`input.skill` 包含 skill 名称。

微信转发时显示为：`🔧 调用工具：[Skill] - chat-history`。

---

## 15. 辅助入口脚本

### 15.1 `run_chat.py`

纯终端交互模式，直接与 Claude Code 对话，不涉及微信。适合调试 Claude CLI 通信。

```
python run_chat.py                    # 交互式终端
python run_chat.py "帮我写快速排序"    # 单次询问
```

### 15.2 `wechat-claude-bridge.mjs`

Node.js 版本 demo，基于 `@anthropic-ai/claude-agent-sdk`。是早期/备选实现，功能不如 Python 版完整。

### 15.3 `start.bat`

Windows 一键启动脚本：切换到项目目录 → 运行 `python run_weixin.py`。
