"""fork 链路（/fork 依赖）：load_user_messages 只取用户消息；fork_session 取选中消息之前的
前缀并落盘（重读不丢历史）、排除选中消息、标题 "[Fork] "+内容（_TITLE_CHARS 截断）；
选中首条消息（前缀为空）不建会话文件/索引；未知 id 返回 None 且不动当前会话。"""
import json

import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager, _TITLE_CHARS as MESSAGE_PREVIEW_CHARS


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def _seed(manager, contents):
    """当前会话依次写入消息（dict 形式），返回写入的 Message 列表"""
    manager.new_session()
    for message in contents:
        manager.add_message(message)
    return manager.session_map[manager.current_session].messages


def _read_back(manager, session_id):
    """清空当前会话指针后 load_session：绕过内存短路，强制从文件重读"""
    manager.reset()
    return manager.load_session(session_id)


def _index_ids(manager):
    """会话索引文件里的会话 id 列表（直接读文件，避免 load_session_list 重置内存会话）"""
    path = session_module.SESSION_INDEX_FILE
    return [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_user_messages_returns_only_user_messages(manager):
    _seed(manager, [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "t1", "tool_call_id": "c1"},
        {"role": "user", "content": "u2"},
    ])
    assert [m.content for m in manager.load_user_messages()] == ["u1", "u2"]


def test_load_user_messages_empty_session_returns_none(manager):
    manager.new_session()
    assert manager.load_user_messages() is None


def test_fork_session_persists_prefix_and_excludes_picked(manager):
    messages = _seed(manager, [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ])
    picked = messages[2]  # 选中第二条用户消息：分叉点 = 它之前
    forked = manager.fork_session(picked.id)

    assert forked is not None and forked.id != picked.id
    assert [m.content for m in forked.messages] == ["u1", "a1"]
    assert forked.title == "[Fork] u2"
    # 已落盘：从文件重读仍是同一前缀（而非内存副本）
    assert [m.content for m in _read_back(manager, forked.id).messages] == ["u1", "a1"]
    # 分叉后追加消息，前缀仍在（append 不会覆盖 rewrite 落盘的历史）
    manager.add_message({"role": "user", "content": "u2-edited"})
    assert [m.content for m in _read_back(manager, forked.id).messages] == ["u1", "a1", "u2-edited"]


def test_fork_session_from_first_user_message_creates_no_file_or_index(manager):
    messages = _seed(manager, [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
    forked = manager.fork_session(messages[0].id)  # 选中首条消息：前缀为空

    assert forked is not None and forked.messages == []
    assert manager.current_session == forked.id
    assert not (session_module.SESSION_DIR / forked.id).exists(), "空 fork 不应建会话文件"
    assert forked.id not in _index_ids(manager), "空 fork 不应写会话索引"

    # 首条消息落盘时才建档、入索引
    manager.add_message({"role": "user", "content": "u1-edited"})
    assert (session_module.SESSION_DIR / forked.id).exists()
    assert forked.id in _index_ids(manager)
    assert [m.content for m in _read_back(manager, forked.id).messages] == ["u1-edited"]


def test_fork_session_picked_from_list_loads_history(manager):
    """Session 窗口路径：load_session_list 刷新索引（只含元数据）会重置内存会话；
    窗口里选中当前（fork）会话时 load_session 必须从文件重读，渲染完整历史而非空会话。"""
    messages = _seed(manager, [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ])
    forked = manager.fork_session(messages[2].id)

    manager.load_session_list()  # 等价于 _open_sessions / SessionPickerScreen._reload 的刷新
    picked = manager.load_session(forked.id)  # 窗口里选中的正是当前（fork）会话

    assert picked is not None and picked.title == "[Fork] u2"
    assert [m.content for m in picked.messages] == ["u1", "a1"], "选中 fork 会话应渲染其落盘历史"


def test_fork_session_title_truncates_and_flattens(manager):
    content = "y" * 10 + "\n" + "x" * (MESSAGE_PREVIEW_CHARS + 5)
    messages = _seed(manager, [{"role": "user", "content": "u1"},
                               {"role": "assistant", "content": "a1"},
                               {"role": "user", "content": content}])
    forked = manager.fork_session(messages[2].id)
    flat = "y" * 10 + " " + "x" * (MESSAGE_PREVIEW_CHARS + 5)
    assert forked.title == "[Fork] " + flat[:MESSAGE_PREVIEW_CHARS] + "…"


def test_fork_session_unknown_id_returns_none_and_keeps_current(manager):
    messages = _seed(manager, [{"role": "user", "content": "u1"}])
    before = manager.current_session
    assert manager.fork_session("no-such-id") is None
    assert manager.current_session == before
    assert [m.content for m in manager.session_map[before].messages] == ["u1"]


def test_fork_session_without_current_session_returns_none(manager):
    assert manager.fork_session("any") is None
