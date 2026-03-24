# WeChat–Claude Code Bridge

[中文文档](README_cn.md)

A bridge service that connects WeChat to Claude Code. When a WeChat message is received, it is processed through the Claude Code CLI and the response is sent back via WeChat.

---

## Requirements

| Dependency | Min Version | Install |
|------------|-------------|---------|
| **Node.js** | 18+ | https://nodejs.org |
| **Claude Code CLI** | Latest | `npm install -g @anthropic-ai/claude-code` |
| **Python** | 3.10+ | https://www.python.org or conda |
| **qrcode** (Python, optional) | — | `pip install qrcode` |

### Prerequisites

1. A valid Anthropic API Key (or already authenticated via the `claude` command).
2. Verify the `claude` CLI is available:
   ```bash
   claude --version
   ```

---

## Installation

### 1. Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

First-time login:

```bash
claude
```

Follow the prompts to authenticate.

### 2. Install Python Dependencies

```bash
pip install qrcode
```

> `qrcode` is optional — it renders a QR code in the terminal. Without it, a URL will be printed instead.

### 3. Verify Setup

```bash
python -c "from weixin_lib import WeixinClaudeBridge; print('OK')"
```

If it prints `OK`, you're good to go.

---

## Usage

### Quick Start

```bash
python run_weixin.py
```

On first launch a QR code is displayed — scan it with WeChat to log in. The token is saved to `.weixin-token.json` and reused on subsequent runs.

### CLI Arguments

| Argument | Description |
|----------|-------------|
| `--login` | Force re-login (show QR code again) |
| `--config <path>` | Use a custom config file |

Examples:

```bash
python run_weixin.py --login
python run_weixin.py --config my_config.json
```

---

## Configuration

The default config lives at `weixin_lib/default_config.json`. To customize, copy it to the project root:

```bash
cp weixin_lib/default_config.json weixin_config.json
```

### Config Reference

```jsonc
{
  // Which Claude events to forward to WeChat
  "forward_events": {
    "thinking": false,     // Thinking process (usually long, recommended off)
    "text": true,          // Text content fragments
    "tool_use": true,      // Tool invocations (Bash, Read, etc.)
    "tool_result": false,  // Tool output (usually long, recommended off)
    "result": true         // Final answer (always sent regardless of this setting)
  },

  // Prefix labels shown in WeChat for each event type
  "message_prefix": {
    "thinking": "💭 Thinking: ",
    "text": "",
    "tool_use": "🔧 Tool: ",
    "tool_result": "📋 Result: ",
    "result": ""
  },

  // Max characters per WeChat message (longer messages are split)
  "max_message_length": 2000,

  // Claude Code settings
  "claude": {
    "cwd": null,              // Working directory (null = project root)
    "permissions_path": null  // Permission config path (null = default)
  },

  // Token persistence file path
  "token_file": ".weixin-token.json"
}
```

---

## Scheduled Tasks

The bridge includes a built-in scheduler for timed reminders and recurring tasks. In practice, you just need to tell the AI in natural language (e.g. "remind me at 3pm to join the meeting") — it will handle the rest automatically.

Under the hood, the CLI interface is:

```bash
# One-time reminder (time only → today; full datetime → specific date)
python weixin_lib/schedule_cli.py add --time "15:00" --message "Meeting reminder"
python weixin_lib/schedule_cli.py add --time "2025-05-03 15:00" --message "Submit report"

# Recurring task (cron expression: min hour day month weekday)
python weixin_lib/schedule_cli.py add --cron "0 9 * * 1-5" --message "Weekday morning briefing"

# List tasks
python weixin_lib/schedule_cli.py list

# Delete a task
python weixin_lib/schedule_cli.py delete --id <task_id>
```

---

## Project Structure

```
├── run_weixin.py              # Main entry point
├── run_chat.py                # Local Claude chat terminal (for testing)
├── start.bat                  # Windows quick-start script
├── CLAUDE.md                  # Copilot instructions
│
├── CC_lib/                    # Claude Code CLI wrapper
│   ├── claude_cli.py          # ClaudeChat persistent process manager
│   └── permissions.json       # Tool permission config
│
├── weixin_lib/                # WeChat bridge library
│   ├── __init__.py
│   ├── ilink_api.py           # iLink HTTP API client
│   ├── bridge.py              # Core bridge logic
│   ├── config.py              # Config management
│   ├── logger.py              # Logging setup
│   ├── scheduler.py           # Scheduled task engine
│   ├── schedule_cli.py        # CLI for managing scheduled tasks
│   └── default_config.json    # Default config
│
├── data/                      # Runtime data (tasks, etc.)
└── LOG/                       # Log files
```

---

## Troubleshooting

### "Session expired" after scanning QR code

```bash
python run_weixin.py --login
```

Re-scan the QR code to get a fresh session.

### Claude not responding or throwing errors

Make sure the Claude Code CLI works on its own:

```bash
claude -p "Hello"
```

Fix any CLI issues before running the bridge.

### QR code not showing in terminal

Install the qrcode library:

```bash
pip install qrcode
```

Alternatively, copy the URL printed in the terminal and open it in a browser.
