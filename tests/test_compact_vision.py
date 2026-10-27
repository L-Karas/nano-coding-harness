"""压缩请求也走视觉门控：非视觉模型下整条图片消息变纯文本占位。"""
import asyncio

import core.context.compact.context_compact as compact_mod
from core.context.message import Message
from core.template import IMAGE_OMITTED_MESSAGE, INJECTION_MESSAGES_TEMPLATE
from tests.support import FakeStream, StubModelClient


def _history():
    return [Message(role="user", content=[
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content="img")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ])]


def _summary_request(monkeypatch, vision):
    client = StubModelClient(vision=vision, response=FakeStream())
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: client)
    asyncio.run(compact_mod.summarize_history(_history()))
    return client.calls[0]["messages"][0]


def test_summary_replaces_image_message_without_vision(monkeypatch):
    assert _summary_request(monkeypatch, False) == {
        "role": "user",
        "content": IMAGE_OMITTED_MESSAGE,
    }


def test_summary_keeps_image_message_with_vision(monkeypatch):
    message = _summary_request(monkeypatch, True)
    assert message["content"][0]["type"] == "text"
    assert message["content"][1]["type"] == "image_url"
