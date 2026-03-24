# 项目说明

本项目是一个微信-Claude Code 桥接服务。

## 输出格式要求

你的回复最终会发送到微信，微信不支持 Markdown 渲染。请遵守以下规则：
- 不要使用 #标题、**粗体**、*斜体*、`代码`、[链接](url) 等 Markdown 语法
- 用纯文本方式排版，确保用户在微信中阅读清晰易懂
- 善用换行、缩进、序号、符号等纯文本手段来组织内容结构

## 定时任务

当用户要求设置定时提醒或周期任务时，**必须使用 `weixin_lib/schedule_cli.py`**，不要使用 CronCreate 或 crontab（Windows 不支持 cron）。

用法：
```bash
# 一次性提醒（仅时间 → 今天，完整日期时间 → 指定日期）
python weixin_lib/schedule_cli.py add --time "15:00" --message "提醒内容"
python weixin_lib/schedule_cli.py add --time "2025-05-03 15:00" --message "提醒内容"

# 周期任务（cron 表达式：分 时 日 月 周几）
python weixin_lib/schedule_cli.py add --cron "0 9 * * 1-5" --message "工作日早报"

# 查看任务
python weixin_lib/schedule_cli.py list

# 取消任务
python weixin_lib/schedule_cli.py delete --id <任务ID>
```

目标用户由系统自动处理，不需要指定。



注意保持输出格式符合微信纯文本要求，避免使用Markdown。
