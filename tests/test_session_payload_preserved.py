"""update_messages 回写不得抹掉 tool 消息 payload（diff 记录）——回归保护。"""

import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def _seed(manager):
    manager.add_message({"role": "user", "content": "fix bug"})
    manager.add_message({
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"}}],
    })
    manager.add_message({
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "written ok",
        "payload": [["+", 1, "line"], ["-", 2, "old"]],
    })


def test_payload_survives_rewrite(manager):
    """compact 类回写：micro_compact 改动正文后 diff 不得丢。"""
    _seed(manager)
    messages = manager.load_messages()  # 模拟 compact / prepare_messages 的入参来源
    assert messages[-1].payload == [["+", 1, "line"], ["-", 2, "old"]]
    messages[-1].content = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 行为
    manager.update_messages(messages)

    persisted = manager.load_session(manager.current_session)
    assert persisted.messages[-1].role == "tool"
    assert persisted.messages[-1].content == "[Old tool result content cleared. Re-run if needed.]"
    assert persisted.messages[-1].payload == [["+", 1, "line"], ["-", 2, "old"]]


def test_payload_dropped_only_with_message(manager):
    """compact_history 全量摘要：消息被删除时 payload 随消息消失，属预期且不报错。"""
    _seed(manager)
    manager.update_messages([{"role": "user", "content": "<compacted-messages>summary</compacted-messages>"}])

    message = manager.session_map[manager.current_session].messages[0]
    assert message.role == "user" and message.content == "<compacted-messages>summary</compacted-messages>"
    assert message.payload == ""
