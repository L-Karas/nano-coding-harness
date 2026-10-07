"""update_messages 回写不得重新生成已有消息 id（/fork 等外部引用依赖 id 稳定）——回归保护。"""

import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def _seed(manager):
    manager.add_message({"role": "user", "content": "first"})
    manager.add_message({
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"}}],
    })
    manager.add_message({"role": "tool", "tool_call_id": "call_1", "content": "written ok"})
    manager.add_message({"role": "user", "content": "second"})


def _ids(manager):
    return [m.id for m in manager.session_map[manager.current_session].messages]


def test_id_survives_rewrite_and_fork_still_works(manager):
    """回写后 id 不变；拿着回写前的 id 仍能 fork（旧引用不失效）。"""
    _seed(manager)
    old_ids = _ids(manager)
    original_session = manager.current_session
    stripped = manager.load_messages()
    stripped[2]["content"] = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 类改动
    manager.update_messages(stripped)

    assert _ids(manager) == old_ids

    forked = manager.fork_session(old_ids[3])
    assert forked is not None
    assert [m.id for m in forked.messages] == old_ids[:3]
    # 切回原会话（从磁盘重读）id 亦不变
    assert [m.id for m in manager.load_session(original_session).messages] == old_ids


def test_explicit_id_wins_over_old(manager):
    """入参显式携带 id 时以入参为准，旧值不覆盖新值。"""
    _seed(manager)
    stripped = manager.load_messages()
    stripped[0]["id"] = "message-explicit"
    manager.update_messages(stripped)

    assert _ids(manager)[0] == "message-explicit"


def test_new_message_gets_new_id(manager):
    """compact 摘要等新消息没有旧消息对应，分配新 id。"""
    _seed(manager)
    old_ids = _ids(manager)
    manager.update_messages([{"role": "user", "content": "<compacted_messages>summary</compacted_messages>"}])

    new_id = _ids(manager)[0]
    assert new_id not in old_ids
