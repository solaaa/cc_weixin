---
name: chat-history
description: Search and retrieve past conversation history with the user. Use this skill whenever the user mentions something discussed previously, asks about past conversations, references earlier requests, wants to recall information from a previous chat, or when you need historical context to answer accurately. Also use this skill when you need to create daily summaries of conversations. Even if the user doesn't explicitly say "search history", trigger this skill when they use phrases like "之前聊过", "上次说的", "我之前让你", "记得吗", "以前提到过" or similar references to past interactions.
---

# Chat History Search

This project maintains a SQLite-based conversation history that stores all meaningful exchanges between the user and the Agent. The history uses a two-level retrieval system: summaries (table of contents) and messages (full content).

## When to Use

- User references past conversations ("之前聊过的", "上次你帮我查的", "我之前说的那个")
- User asks you to recall something ("记得吗", "你还记得吗")
- You need historical context to give an accurate answer
- You need to create a daily summary of conversations

## Two-Level Retrieval Process

Always follow this order — search summaries first, then drill into detail:

### Step 1: Search Summaries (Browse the Table of Contents)

```bash
python weixin_lib/chat_history_cli.py search-summaries --query "关键词"
```

You can also filter by date range:
```bash
python weixin_lib/chat_history_cli.py search-summaries --query "关键词" --from 2025-01-01 --to 2025-01-31
```

This returns a list of summaries with their IDs. Each summary represents a group of related messages from a specific date.

### Step 2: Get Messages for a Summary (Read the Content)

After identifying relevant summaries, retrieve the actual messages:

```bash
python weixin_lib/chat_history_cli.py get-messages --summary-id <ID>
```

This returns the original user questions and Agent replies associated with that summary.

## Helper Commands

### List dates with chat records
```bash
python weixin_lib/chat_history_cli.py dates
```

### View all messages for a specific date
```bash
python weixin_lib/chat_history_cli.py messages-by-date --date 2025-05-03
```

## Creating Summaries

When a day's conversations are done (or when explicitly triggered), create a summary to make future searches efficient:

```bash
python weixin_lib/chat_history_cli.py create-summary --date 2025-05-03 --text "讨论了旅行计划；预订了神户酒店；查询了签证材料"
```

The summary text should be a concise description of the key topics discussed that day, separated by semicolons. Keep it under 200 characters. This text is what gets searched in Step 1, so include the important keywords.

## Important Notes

- Never dump all history at once — always use the two-step process
- When searching, try broad keywords first; narrow down if too many results
- Summaries may not exist for recent dates — in that case, use `messages-by-date` as a fallback
- If the user's question is ambiguous about timing, check `dates` first to see what's available
