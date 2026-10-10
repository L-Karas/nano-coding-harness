"""update_messages 回写不得抹掉消息已有的 usage（token 统计）——回归保护。"""

from dataclasses import asdict

import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5, "total_tokens": 120}


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
        "usage": dict(USAGE),
    })
    manager.add_message({"role": "tool", "tool_call_id": "call_1", "content": "written ok"})


def _assistant_usage(manager):
    assistant = next(m for m in manager.session_map[manager.current_session].messages if m.role == "assistant")
    return asdict(assistant)["usage"]


def test_usage_survives_rewrite(manager):
    """prepare_messages / compact 类回写：micro_compact 改动正文后统计不得清零。"""
    _seed(manager)
    messages = manager.load_messages()
    assert messages[1].usage == USAGE
    messages[-1].content = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 行为
    manager.update_messages(messages)

    assert _assistant_usage(manager) == USAGE
    assert asdict(manager.load_session(manager.current_session).messages[1])["usage"] == USAGE  # 落盘后仍保留


def test_explicit_usage_wins_over_old(manager):
    """入参显式携带 usage 时以入参为准。"""
    _seed(manager)
    messages = manager.load_messages()
    messages[1].usage = USAGE | {"prompt_tokens": 999}
    manager.update_messages(messages)

    assert _assistant_usage(manager)["prompt_tokens"] == 999


def test_usage_dropped_only_with_message(manager):
    """compact_history 全量摘要：消息被删除时 usage 随消息消失，摘要消息不得继承旧统计。"""
    _seed(manager)
    manager.update_messages([{"role": "user", "content": "<compacted_messages>summary</compacted_messages>"}])

    messages = manager.session_map[manager.current_session].messages
    assert len(messages) == 1
    assert asdict(messages[0])["usage"] == {}
