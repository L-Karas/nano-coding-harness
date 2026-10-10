"""SessionManager 契约：Message 货币、只读 current_session、reset()、load_messages 无写副作用。"""
import pytest

import core.context.session.session as session_module
from core.context.message import Message
from core.context.session.session import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def test_load_messages_without_session_is_pure_read(manager):
    assert manager.load_messages() == []
    assert manager.current_session == ""
    assert not (session_module.SESSION_INDEX_FILE).exists(), "读不应落索引"


def test_current_session_is_read_only(manager):
    with pytest.raises(AttributeError):
        manager.current_session = "session-x.jsonl"


def test_reset_clears_current_session(manager):
    manager.new_session()
    assert manager.current_session
    manager.reset()
    assert manager.current_session == ""
    assert manager.load_messages() == []


def test_add_message_accepts_message_object_and_dict(manager):
    manager.add_message(Message(role="user", content="typed"))
    manager.add_message({"role": "assistant", "content": "plain"})
    messages = manager.load_messages()
    assert [(m.role, m.content) for m in messages] == [("user", "typed"), ("assistant", "plain")]


def test_update_messages_normalizes_and_keeps_objects(manager):
    manager.add_message({"role": "user", "content": "fix"})
    messages = manager.load_messages()
    assert all(isinstance(m, Message) for m in messages)
    messages[0].payload = [["+", 1, "line"]]
    manager.update_messages(messages)
    assert manager.load_messages()[0].payload == [["+", 1, "line"]]


def test_message_roundtrip_preserves_all_fields(manager):
    manager.add_message(Message(role="assistant", content="a", reasoning_content="r",
                                tool_calls=[{"id": "c1", "type": "function",
                                             "function": {"name": "read_file", "arguments": "{}"}}],
                                usage={"total_tokens": 5}, payload={"k": "v"}))
    manager.reset()
    reloaded = manager.load_session(manager.session_map and next(iter(manager.session_map)))
    assert reloaded is not None
    message = reloaded.messages[0]
    assert message.reasoning_content == "r" and message.usage == {"total_tokens": 5}
    assert message.payload == {"k": "v"} and message.tool_calls[0]["id"] == "c1"
