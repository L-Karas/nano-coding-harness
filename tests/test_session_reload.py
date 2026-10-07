"""add_message（append 写入）建出的会话必须能重读：文件为空时 append 会先写一个换行，
首行是空行；load_session 解析须跳过空行——否则重启后所有普通会话都读不回（返回 None）。"""

import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def test_reload_append_created_session(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "u1"})
    manager.add_message({"role": "assistant", "content": "a1"})
    sid = manager.current_session

    manager.current_session = ""  # 绕过内存短路，强制从文件重读
    reloaded = manager.load_session(sid)
    assert reloaded is not None
    assert [(m.role, m.content) for m in reloaded.messages] == [("user", "u1"), ("assistant", "a1")]
