"""update_messages 回写不得抹掉消息已有的 usage（token 统计）——回归保护。"""

from dataclasses import asdict

from tests.support import seed_tool_cycle

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5, "total_tokens": 120}


def _assistant_usage(session_manager):
    assistant = next(m for m in session_manager.session_map[session_manager.current_session].messages if m.role == "assistant")
    return asdict(assistant)["usage"]


def test_usage_survives_rewrite(session_manager):
    """prepare_messages / compact 类回写：micro_compact 改动正文后统计不得清零。"""
    seed_tool_cycle(session_manager, assistant_usage=USAGE)
    messages = session_manager.load_messages()
    assert messages[1].usage == USAGE
    messages[-1].content = "[Old tool result content cleared. Re-run if needed.]"  # micro_compact 行为
    session_manager.update_messages(messages)

    assert _assistant_usage(session_manager) == USAGE
    assert asdict(session_manager.load_session(session_manager.current_session).messages[1])["usage"] == USAGE  # 落盘后仍保留


def test_explicit_usage_wins_over_old(session_manager):
    """入参显式携带 usage 时以入参为准。"""
    seed_tool_cycle(session_manager, assistant_usage=USAGE)
    messages = session_manager.load_messages()
    messages[1].usage = USAGE | {"prompt_tokens": 999}
    session_manager.update_messages(messages)

    assert _assistant_usage(session_manager)["prompt_tokens"] == 999


def test_usage_dropped_only_with_message(session_manager):
    """compact_history 全量摘要：消息被删除时 usage 随消息消失，摘要消息不得继承旧统计。"""
    seed_tool_cycle(session_manager, assistant_usage=USAGE)
    session_manager.update_messages([{"role": "user", "content": "<compacted_messages>summary</compacted_messages>"}])

    messages = session_manager.session_map[session_manager.current_session].messages
    assert len(messages) == 1
    assert asdict(messages[0])["usage"] == {}
