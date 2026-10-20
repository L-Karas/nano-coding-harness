"""TUI 对 content 数组的兼容：注入消息跳过、用户卡片压平、fork 预览压平。"""
import core.tui.render as render
from core.context.message import Message
from core.context.session.session import Session
from core.template import INJECTION_MESSAGES_TEMPLATE
from core.tui.screens.fork import _preview


class _App:
    def __init__(self):
        self.cards = []

    def _add_card(self, kind, body, **kwargs):
        self.cards.append((kind, body))


def test_render_user_input_flattens_array(monkeypatch):
    app = _App()
    monkeypatch.setattr(render, "_exec", lambda fn: fn(app))

    render.render_user_input([
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "x"}},
    ])

    assert app.cards[0][0] == "user"
    assert "look" in app.cards[0][1].plain


def test_replay_skips_injected_image_message(monkeypatch):
    events = []
    monkeypatch.setattr(render, "render_user_input",
                        lambda text: events.append(("user", text)))
    monkeypatch.setattr(render, "render_assistant_response",
                        lambda text: events.append(("assistant", text)))
    session = Session(messages=[Message(role="user", content=[
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content="images")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ])])

    render.render_session_history(session)

    assert events == []


def test_preview_flattens_array_content():
    assert _preview([{"type": "text", "text": "a\nb   c"}]) == "a b c"


def test_non_vision_image_message_is_recognized_and_skipped(monkeypatch):
    from core.agent_step import _image_followup_message
    from core.context.message import is_injected_content
    from core.tools import ImageAttachment

    message = _image_followup_message(
        [ImageAttachment(source="/tmp/a.png", mime="image/png",
                         image_url="data:image/png;base64,AAA")],
        vision=False,
    )
    assert is_injected_content(message["content"])

    events = []
    monkeypatch.setattr(render, "render_user_input",
                        lambda text: events.append(("user", text)))
    render.render_session_history(Session(messages=[Message(**message)]))
    assert events == []
