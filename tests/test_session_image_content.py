"""会话 content 数组：落盘 / 重读、标题、fork 列表与标题。"""

from core.template import INJECTION_MESSAGES_TEMPLATE
from tests.support import image_content


def test_image_content_round_trips(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "look"})
    session_manager.add_message({"role": "user", "content": image_content()})
    session_id = session_manager.current_session

    session_manager.reset()
    reloaded = session_manager.load_session(session_id)

    assert reloaded is not None
    assert reloaded.messages[-1].content == image_content()


def test_synthetic_image_message_does_not_overwrite_title(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "fix sidebar"})
    session_manager.add_message({"role": "user", "content": image_content()})

    assert session_manager.session_map[session_manager.current_session].title == "fix sidebar"


def test_fork_list_excludes_synthetic_user_messages(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "real request"})
    session_manager.add_message({"role": "user", "content": image_content()})
    session_manager.add_message({"role": "user",
                                 "content": INJECTION_MESSAGES_TEMPLATE.format(content="cron")})
    session_manager.add_message({"role": "user",
                                 "content": "<compacted_messages>\nsummary\n</compacted_messages>"})

    assert [m.content for m in session_manager.load_user_messages()] == ["real request"]


def test_skill_invocation_sets_title_and_is_forkable(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "[Invoke skill 'review']"})
    session_manager.add_message({"role": "user", "content": "[Invoke skill 'review'] auth"})

    assert session_manager.session_map[session_manager.current_session].title == "[Invoke skill 'review'] auth"
    assert [m.content for m in session_manager.load_user_messages()] == [
        "[Invoke skill 'review']",
        "[Invoke skill 'review'] auth",
    ]


def test_fork_title_flattens_array_content(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "first"})
    session_manager.add_message({"role": "user",
                                 "content": [{"type": "text", "text": "look at\nthis shot"}]})
    messages = session_manager.session_map[session_manager.current_session].messages

    forked = session_manager.fork_session(messages[1].id)

    assert forked is not None
    assert forked.title == "[Fork] look at this shot"


def test_array_user_content_sets_title(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user",
                                 "content": [{"type": "text", "text": "describe this"}]})

    assert session_manager.session_map[session_manager.current_session].title == "describe this"
