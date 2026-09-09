import threading

from core.config import HARNESS_CONFIG_DIR
from core.agent_loop import cron_auto_loop, AGENT_LOCK, agent_loop
from core.mcp.mcp import warmup
from core.protocol_state import consume_lead_inbox
from core.session.session import SESSION_MANAGER
from core.template import INJECTION_MESSAGES_PREFIX, INJECTION_MESSAGES_SUFFIX
from core.tui.ui_textual import run


if not HARNESS_CONFIG_DIR.exists():
    HARNESS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def make_agent_turn():
    """构造每轮用户消息的处理函数：与 cron 串行执行完整 agent 回合，再吸收 lead inbox 消息。

    由 Textual UI 在后台线程调用；用户消息已由 UI 写入会话管理器。
    """
    def agent_turn(_query: str) -> None:
        with AGENT_LOCK:
            agent_loop()
        # context.update(update_context(context, messages))

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
                "content": INJECTION_MESSAGES_PREFIX + f"[Inbox messages]\n{inbox_text}" + INJECTION_MESSAGES_SUFFIX
            })

    return agent_turn


def main():
    # 后台线程预热 MCP 连接（阻塞至就绪或失败），避免首个 agent 轮次被慢建连卡住
    threading.Thread(target=warmup, name="mcp-warmup", daemon=True).start()
    threading.Thread(target=cron_auto_loop, daemon=True).start()
    run(handle_query=make_agent_turn(), session_manager=SESSION_MANAGER)


if __name__ == "__main__":
    main()
