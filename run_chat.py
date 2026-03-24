"""Claude Code 交互终端。"""

import sys
sys.path.insert(0, "CC_lib")
from CC_lib.claude_cli import ClaudeChat, format_event


def run_stream(chat, message):
    """运行一轮流式对话，处理 AskUserQuestion 回调。"""
    for ev in chat.stream(message):
        t = ev.get("type", "")

        # AskUserQuestion 回调
        if t == "ask_user":
            text = format_event(ev)
            if text:
                print(text)
            try:
                user_reply = input("  你的回答: ").strip()
            except (EOFError, KeyboardInterrupt):
                user_reply = ""
            if user_reply:
                # 继续对话
                for sub_ev in chat.answer(user_reply):
                    sub_text = format_event(sub_ev)
                    if sub_text:
                        print(sub_text)
            continue

        text = format_event(ev)
        if text:
            print(text)


def main():
    chat = ClaudeChat()

    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        print(f">>> {prompt}\n")
        run_stream(chat, prompt)
        chat.stop()
    else:
        print("=" * 50)
        print("Claude Code 交互终端")
        print("命令: quit=退出, restart=重启进程")
        print("=" * 50)
        print()
        while True:
            try:
                user_input = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break
            if user_input.lower() == "restart":
                chat.stop()
                print("[进程已重启]\n")
                continue

            print()
            run_stream(chat, user_input)
            print()

        chat.stop()


if __name__ == "__main__":
    main()
