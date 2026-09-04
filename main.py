import threading

from core.config import HARNESS_CONFIG_DIR
from core.agent_loop import cron_auto_loop, AGENT_LOCK, agent_loop
from core.context import update_context
from core.protocol_state import consume_lead_inbox
from core.session.session import SESSION_MANAGER
from core.tui.ui_textual import run

if not HARNESS_CONFIG_DIR.exists():
    HARNESS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

_BANNER = ("🤖 Nano-Harness Agent Loop", "Enter a question, press Enter to send. Type /exit or q to quit.")


def make_agent_turn(messages: list, context: dict):
    """构造每轮用户消息的处理函数：与 cron 串行执行完整 agent 回合，再吸收 lead inbox 消息。

    由 Textual UI 在后台线程调用；用户消息已由 UI 写入会话管理器。
    """
    def agent_turn(_query: str) -> None:
        with AGENT_LOCK:
            agent_loop(messages, context)
        context.update(update_context(context, messages))

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
            SESSION_MANAGER.add_message({
                "role": "user",
                "content": f"[Inbox messages]\n{inbox_text}"
            })

    return agent_turn


def main():
    messages = []
    context = update_context({}, [])
    threading.Thread(target=cron_auto_loop, args=(messages, context), daemon=True).start()
    run(handle_query=make_agent_turn(messages, context), session_manager=SESSION_MANAGER, banner=_BANNER)


if __name__ == "__main__":
    main()
