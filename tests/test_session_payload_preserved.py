"""update_messages 回写不得抹掉 tool 消息 payload（diff 记录）——回归保护。"""

from tests.support import seed_tool_cycle

PAYLOAD = [["+", 1, "line"], ["-", 2, "old"]]


def test_payload_survives_rewrite(session_manager):
    """compact 类回写：micro_compact 改动正文后 diff 不得丢。"""
    seed_tool_cycle(session_manager, tool_payload=PAYLOAD)
    messages = session_manager.load_messages()  # 模拟 compact / prepare_messages 的入参来源
    assert messages[-1].payload == PAYLOAD
    messages[-1].content = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 行为
    session_manager.update_messages(messages)

    persisted = session_manager.load_session(session_manager.current_session)
    assert persisted.messages[-1].role == "tool"
    assert persisted.messages[-1].content == "[Old tool result content cleared. Re-run if needed.]"
    assert persisted.messages[-1].payload == PAYLOAD


def test_payload_dropped_only_with_message(session_manager):
    """compact_history 全量摘要：消息被删除时 payload 随消息消失，属预期且不报错。"""
    seed_tool_cycle(session_manager, tool_payload=PAYLOAD)
    session_manager.update_messages([{"role": "user", "content": "<compacted-messages>summary</compacted-messages>"}])

    message = session_manager.session_map[session_manager.current_session].messages[0]
    assert message.role == "user" and message.content == "<compacted-messages>summary</compacted-messages>"
    assert message.payload == ""
