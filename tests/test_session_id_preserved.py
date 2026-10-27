"""update_messages 回写不得重新生成已有消息 id（/fork 等外部引用依赖 id 稳定）——回归保护。"""

from tests.support import seed_tool_cycle


def _seed(session_manager):
    seed_tool_cycle(session_manager)
    session_manager.add_message({"role": "user", "content": "second"})


def _ids(session_manager):
    return [m.id for m in session_manager.session_map[session_manager.current_session].messages]


def test_id_survives_rewrite_and_fork_still_works(session_manager):
    """回写后 id 不变；拿着回写前的 id 仍能 fork（旧引用不失效）。"""
    _seed(session_manager)
    old_ids = _ids(session_manager)
    original_session = session_manager.current_session
    stripped = session_manager.load_messages()
    stripped[2].content = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 类改动
    session_manager.update_messages(stripped)

    assert _ids(session_manager) == old_ids

    forked = session_manager.fork_session(old_ids[3])
    assert forked is not None
    assert [m.id for m in forked.messages] == old_ids[:3]
    # 切回原会话（从磁盘重读）id 亦不变
    assert [m.id for m in session_manager.load_session(original_session).messages] == old_ids


def test_explicit_id_wins_over_old(session_manager):
    """入参显式携带 id 时以入参为准，旧值不覆盖新值。"""
    _seed(session_manager)
    stripped = session_manager.load_messages()
    stripped[0].id = "message-explicit"
    session_manager.update_messages(stripped)

    assert _ids(session_manager)[0] == "message-explicit"


def test_new_message_gets_new_id(session_manager):
    """compact 摘要等新消息没有旧消息对应，分配新 id。"""
    _seed(session_manager)
    old_ids = _ids(session_manager)
    session_manager.update_messages([{"role": "user", "content": "<compacted_messages>summary</compacted_messages>"}])

    new_id = _ids(session_manager)[0]
    assert new_id not in old_ids
