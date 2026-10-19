"""压缩请求也走视觉门控：非视觉模型下整条图片消息变纯文本占位。"""
import asyncio

import core.context.compact.context_compact as compact_mod
from core.context.message import Message
from core.template import IMAGE_OMITTED_MESSAGE, INJECTION_MESSAGES_TEMPLATE


class _Stream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        pass


class _Client:
    def __init__(self, vision):
        self.vision = vision
        self.kwargs = None

    def supports_vision(self):
        return self.vision

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self):
        async def create(**kwargs):
            self.kwargs = kwargs
            return _Stream()
        return create


def _history():
    return [Message(role="user", content=[
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content="img")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ])]


def _summary_request(monkeypatch, vision):
    client = _Client(vision)
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: client)
    asyncio.run(compact_mod.summarize_history(_history()))
    return client.kwargs["messages"][0]


def test_summary_replaces_image_message_without_vision(monkeypatch):
    assert _summary_request(monkeypatch, False) == {
        "role": "user",
        "content": IMAGE_OMITTED_MESSAGE,
    }


def test_summary_keeps_image_message_with_vision(monkeypatch):
    message = _summary_request(monkeypatch, True)
    assert message["content"][0]["type"] == "text"
    assert message["content"][1]["type"] == "image_url"
