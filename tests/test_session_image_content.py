"""会话 content 数组：落盘 / 重读、标题、fork 列表与标题。"""
import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager
from core.template import INJECTION_MESSAGES_TEMPLATE


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def _image_content(text="images"):
    return [
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content=text)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def test_image_content_round_trips(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "look"})
    manager.add_message({"role": "user", "content": _image_content()})
    session_id = manager.current_session

    manager.reset()
    reloaded = manager.load_session(session_id)

    assert reloaded is not None
    assert reloaded.messages[-1].content == _image_content()


def test_synthetic_image_message_does_not_overwrite_title(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "fix sidebar"})
    manager.add_message({"role": "user", "content": _image_content()})

    assert manager.session_map[manager.current_session].title == "fix sidebar"


def test_fork_list_excludes_synthetic_user_messages(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "real request"})
    manager.add_message({"role": "user", "content": _image_content()})
    manager.add_message({"role": "user",
                         "content": INJECTION_MESSAGES_TEMPLATE.format(content="cron")})
    manager.add_message({"role": "user",
                         "content": "<compacted_messages>\nsummary\n</compacted_messages>"})

    assert [m.content for m in manager.load_user_messages()] == ["real request"]


def test_skill_invocation_sets_title_and_is_forkable(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "[Invoke skill 'review']"})
    manager.add_message({"role": "user", "content": "[Invoke skill 'review'] auth"})

    assert manager.session_map[manager.current_session].title == "[Invoke skill 'review'] auth"
    assert [m.content for m in manager.load_user_messages()] == [
        "[Invoke skill 'review']",
        "[Invoke skill 'review'] auth",
    ]


def test_fork_title_flattens_array_content(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "first"})
    manager.add_message({"role": "user",
                         "content": [{"type": "text", "text": "look at\nthis shot"}]})
    messages = manager.session_map[manager.current_session].messages

    forked = manager.fork_session(messages[1].id)

    assert forked is not None
    assert forked.title == "[Fork] look at this shot"


def test_array_user_content_sets_title(manager):
    manager.new_session()
    manager.add_message({"role": "user",
                         "content": [{"type": "text", "text": "describe this"}]})

    assert manager.session_map[manager.current_session].title == "describe this"
