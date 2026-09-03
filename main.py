import threading

from src.config import HARNESS_CONFIG_DIR
from src.agent_loop import cron_auto_loop, AGENT_LOCK, agent_loop
from src.context import update_context
from src.protocol_state import consume_lead_inbox
from src.session.session import SESSION_MANAGER
from src.tui.ui import (render_banner, get_user_input, render_user_input, clear_screen,
                        render_sessions, select_session, render_session_history)

if not HARNESS_CONFIG_DIR.exists():
    HARNESS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


_BANNER = ("🤖 Nano-Harness Agent Loop", "Enter a question, press Enter to send. Type /exit or q to quit.")


def _reset_ui() -> None:
    """清屏并重绘头部 Banner（/new 与切换会话共用）"""
    clear_screen()
    render_banner(*_BANNER)


def _handle_sessions() -> None:
    """处理 /sessions：交互选择会话（Delete 删除），Enter 切换为当前会话并重放历史"""
    sessions = SESSION_MANAGER.load_session_list()
    selected, remaining = select_session(sessions, on_delete=SESSION_MANAGER.delete_session,
                                         current_session_id=SESSION_MANAGER.current_session)
    if selected is None:
        if not remaining:
            render_sessions([])
        return
    # load_session 会更新 current_session 并载入消息列表
    loaded = SESSION_MANAGER.load_session(selected.id)
    if loaded:
        _reset_ui()
        render_session_history(loaded)


def main():
    render_banner(*_BANNER)

    messages = []
    context = update_context({}, [])
    threading.Thread(target=cron_auto_loop, args=(messages, context), daemon=True).start()

    while True:
        try:
            query = get_user_input()
        except (EOFError, KeyboardInterrupt):
            break

        cmd = query.strip().lower()
        if cmd in ["/exit", "/quit"]:
            break
        if cmd == "/sessions":
            _handle_sessions()
            continue
        if cmd == "/new":
            SESSION_MANAGER.new_session()
            _reset_ui()
            continue

        render_user_input(query)
        # trigger_hooks("UserPromptSubmit", query)
        # messages.append({
        #     "role": "user",
        #     "content": query
        # })
        SESSION_MANAGER.add_message({
            "role": "user",
            "content": query
        })
        # print(SESSION_MANAGER.load_messages())

        with AGENT_LOCK:
            agent_loop(messages, context)
            context = update_context(context, messages)

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

            # messages.append({
            #     "role": "user",
            #     "content": f"[Inbox messages]\n{inbox_text}"
            # })
            SESSION_MANAGER.add_message({
                "role": "user",
                "content": f"[Inbox messages]\n{inbox_text}"
            })

        print()


if __name__ == "__main__":
    main()
