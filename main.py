import logging
import threading

from src.agent_loop import cron_auto_loop, AGENT_LOCK, agent_loop
from src.config import LOG_DIR
from src.context import update_context
from src.hook_permission import trigger_hooks
from src.protocol_state import consume_lead_inbox
from src.ui import render_banner, get_user_input, render_user_input

if not LOG_DIR.exists():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(filename=str(LOG_DIR / ".log"), level=logging.INFO, filemode="w")


def main():
    render_banner("🤖 Nano-Harness Agent Loop", "Enter a question, press Enter to send. Type /exit or q to quit.")

    messages = []
    context = update_context({}, [])
    threading.Thread(target=cron_auto_loop, args=(messages, context), daemon=True).start()

    while True:
        try:
            query = get_user_input()
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ["q", "quit", "exit", "/exit", "/quit"]:
            break

        render_user_input(query)
        trigger_hooks("UserPromptSubmit", query)
        turn_start = len(messages)
        messages.append({
            "role": "user",
            "content": query
        })

        with AGENT_LOCK:
            agent_loop(messages, context)
            context = update_context(context, messages)
            # print_turn_assistants(messages, turn_start)

        inbox = consume_lead_inbox(route_protocol=True)
        if inbox:
            def inbox_label(message):
                request_id = message.get("metadata", {}).get("request_id", "")
                suffix = f"  request_id: {request_id}" if request_id else ""
                return f"{message.get('msg_type', 'message')}{suffix}"

            inbox_text = "\n".join(
                f"From {message['from_agent']} [{inbox_label(message)}]: "
                f"{message['content']}"
                for message in inbox
            )

            messages.append({
                "role": "user",
                "content": f"[Inbox messages]\n{inbox_text}"
            })

        print()


if __name__ == "__main__":
    main()
