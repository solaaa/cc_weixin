# 微信-Claude Code 桥接服务

[English](README.md)

在微信中与 Claude Code 交互。收到微信消息后，通过 Claude Code CLI 处理并将结果回传微信。

---

## 环境要求

| 依赖 | 最低版本 | 安装方式 |
|------|---------|---------|
| **Node.js** | 18+ | https://nodejs.org |
| **Claude Code CLI** | 最新 | `npm install -g @anthropic-ai/claude-code` |
| **Python** | 3.10+ | https://www.python.org 或 conda |
| **qrcode**（Python 库，可选） | — | `pip install qrcode` |

### 前置条件

1. 已有可用的 Anthropic API Key（或已通过 `claude` 命令登录, 或使用 [cc-switch](https://github.com/farion1231/cc-switch)）
2. 确认 `claude` 命令可在终端直接执行：
   ```bash
   claude --version
   ```

---

## 安装步骤

### 1. 安装 Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

首次使用需要登录：

```bash
claude
```

按提示完成认证。
可以使用 [cc-switch](https://github.com/farion1231/cc-switch)中转

### 2. 安装 Python 依赖

```bash
pip install qrcode
```

> `qrcode` 是可选的，用于在终端显示二维码。没有它也能用，会直接打印 URL。

### 3. 验证环境

```bash
python -c "from weixin_lib import WeixinClaudeBridge; print('OK')"
```

输出 `OK` 即可。

---

## 使用

### 一键启动

```bash
python run_weixin.py
```

首次运行会显示二维码，用微信扫码确认登录。登录成功后 token 自动保存到 `.weixin-token.json`，下次启动免扫码。

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--login` | 强制重新扫码登录 |
| `--config <路径>` | 指定自定义配置文件 |

示例：

```bash
python run_weixin.py --login
python run_weixin.py --config my_config.json
```

---

## 配置

默认配置在 `weixin_lib/default_config.json`。自定义方式：

将 `weixin_lib/default_config.json` 复制到项目根目录并重命名为 `weixin_config.json`，然后编辑：

```bash
cp weixin_lib/default_config.json weixin_config.json
```

### 配置项说明

```jsonc
{
  // 控制哪些 Claude 事件回传微信
  "forward_events": {
    "thinking": false,     // 思考过程（通常很长，建议关闭）
    "text": true,          // 正文文本片段
    "tool_use": true,      // 工具调用信息（如调用了 Bash、Read 等）
    "tool_result": false,  // 工具返回的结果（通常很长，建议关闭）
    "result": true         // 最终回答（始终开启，不受此项控制）
  },

  // 各类型消息在微信中的前缀
  "message_prefix": {
    "thinking": "💭 思考中：",
    "text": "",
    "tool_use": "🔧 调用工具：",
    "tool_result": "📋 工具结果：",
    "result": ""
  },

  // 单条微信消息最大字符数（超长自动分片）
  "max_message_length": 2000,

  // Claude Code 配置
  "claude": {
    "cwd": null,              // Claude 工作目录，null 默认为项目目录
    "permissions_path": null  // 权限配置路径，null 使用默认
  },

  // token 持久化文件路径
  "token_file": ".weixin-token.json"
}
```

---

## 定时任务

桥接服务内置了定时提醒和周期任务功能。实际使用时，只需要用自然语言告诉 AI 即可（例如「下午三点提醒我开会」），AI 会自动处理。

底层命令行接口如下：

```bash
# 一次性提醒（仅时间 → 今天，完整日期时间 → 指定日期）
python weixin_lib/schedule_cli.py add --time "15:00" --message "开会提醒"
python weixin_lib/schedule_cli.py add --time "2025-05-03 15:00" --message "提交报告"

# 周期任务（cron 表达式：分 时 日 月 周几）
python weixin_lib/schedule_cli.py add --cron "0 9 * * 1-5" --message "工作日早报"

# 查看任务
python weixin_lib/schedule_cli.py list

# 取消任务
python weixin_lib/schedule_cli.py delete --id <任务ID>
```

---

## 项目结构

```
├── run_weixin.py              # 一键启动入口
├── run_chat.py                # Claude 交互终端（用于本地测试）
├── start.bat                  # Windows 快速启动脚本
├── CLAUDE.md                  # Copilot 指令
│
├── CC_lib/                    # Claude Code CLI 封装
│   ├── claude_cli.py          # ClaudeChat 持久化进程管理
│   └── permissions.json       # 工具权限配置
│
├── weixin_lib/                # 微信桥接库
│   ├── __init__.py
│   ├── ilink_api.py           # iLink HTTP API 客户端
│   ├── bridge.py              # 桥接核心逻辑
│   ├── config.py              # 配置管理
│   ├── logger.py              # 日志配置
│   ├── scheduler.py           # 定时任务引擎
│   ├── schedule_cli.py        # 定时任务命令行工具
│   └── default_config.json    # 默认配置
│
├── data/                      # 运行时数据（任务等）
└── LOG/                       # 日志文件
```

---

## 常见问题

### 二维码扫码后提示"Session 已过期"

```bash
python run_weixin.py --login
```

重新扫码即可。

### Claude 无响应或报错

确认 Claude Code CLI 可正常工作：

```bash
claude -p "你好"
```

如有报错，先解决 CLI 本身的问题。

### 终端不显示二维码

安装 qrcode 库：

```bash
pip install qrcode
```

或者直接复制终端输出的 URL 去浏览器打开。
