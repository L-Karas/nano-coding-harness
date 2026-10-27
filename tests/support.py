"""测试共享替身与小工具：只放跨模块复用的 fake / 断言辅助，避免各测试文件重复造轮子。"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

from core.template import INJECTION_MESSAGES_TEMPLATE


class MemorySession:
    """SessionManager 的最小内存替身（load / update / add 三件套）。"""

    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def load_messages(self):
        return list(self.messages)

    def update_messages(self, messages):
        self.messages = list(messages)

    def add_message(self, message):
        self.messages.append(message)


class StubModelClient:
    """模型客户端替身：记录 create 请求，可控制视觉 / 输出钳制 / compact 预算。"""

    current_provider = "test"

    def __init__(
        self,
        *,
        vision: bool = True,
        max_output: int | None = None,
        clamp_to: int | None = None,
        trigger_tokens: int = 10**9,
        reserve_tokens: int = 0,
        response=None,
    ):
        self.vision = vision
        self.max_output = max_output
        self.clamp_to = clamp_to
        self.trigger_tokens = trigger_tokens
        self.reserve_tokens = reserve_tokens
        self.response = response
        self.calls: list[dict] = []
        self.kwargs: dict | None = None
        self.requested: int | None = None

    def supports_vision(self) -> bool:
        return self.vision

    def clamp_max_tokens(self, requested: int) -> int:
        self.requested = requested
        if self.clamp_to is not None:
            return self.clamp_to
        return requested if self.max_output is None else min(requested, self.max_output)

    def get_model_client(self, **_):
        async def create(**kwargs):
            self.kwargs = kwargs
            self.calls.append(kwargs)
            return self.response if self.response is not None else object()

        return create

    def compact_trigger_tokens(self) -> int:
        return self.trigger_tokens

    def compact_reserve_tokens(self) -> int:
        return self.reserve_tokens


class FakeStream:
    """AsyncOpenAI 流的最小替身：可迭代 chunk，并在退出时 await close()。"""

    def __init__(self, chunks=()):
        self.chunks = list(chunks)

    def __aiter__(self):
        async def gen():
            for chunk in self.chunks:
                yield chunk

        return gen()

    async def close(self):
        pass


def text_chunk(text: str = "", finish_reason=None, usage=None):
    """``streaming_message`` 消费的增量 chunk（正文 / 结束原因 / usage 可选）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text or None, tool_calls=None, reasoning_content=None),
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


def stream_replies(responses):
    """返回按顺序弹出响应的 ``streaming_message`` 替身。"""

    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    return fake_stream


class FakeDDGS:
    """ddgs 客户端替身：``text`` 记录调用参数，``extract`` 返回固定正文。"""

    def __init__(self, results=None, *, captured=None, extract_content="page text"):
        self.results = results if results is not None else [
            {"title": "t", "href": "http://example.com", "body": "b"},
        ]
        self.captured = captured if captured is not None else {}
        self.extract_content = extract_content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, **kwargs):
        self.captured.update(kwargs)
        return self.results

    def extract(self, url):
        self.captured["url"] = url
        return {"url": url, "content": self.extract_content}


def install_fake_ddgs(monkeypatch, **kwargs) -> FakeDDGS:
    """把假 ddgs 模块装进 sys.modules（web_search / web_extract 都在调用时懒导入）。"""
    fake = FakeDDGS(**kwargs)
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=lambda: fake))
    return fake


class RecordingDriver:
    """driver.write 边界替身：只记录写出的底层序列。"""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


def image_content(text: str = "images") -> list[dict]:
    """合成图片用户消息：注入说明 + data URL 图片。"""
    return [
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content=text)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def make_skill(
    name: str = "review",
    description: str = "Review code",
    skill_type: str = "project",
):
    from core.skill.skills import Skill

    return Skill(name=name, description=description, content="",
                 location=f"/skills/{name}", skill_type=skill_type)


def seed_tool_cycle(manager, *, assistant_usage=None, tool_payload=None):
    """向会话写入一轮最小 tool 往返，供 payload / usage 回写回归测试复用。"""
    manager.add_message({"role": "user", "content": "fix bug"})
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"}}],
    }
    if assistant_usage is not None:
        assistant["usage"] = dict(assistant_usage)
    manager.add_message(assistant)
    tool = {"role": "tool", "tool_call_id": "call_1", "content": "written ok"}
    if tool_payload is not None:
        tool["payload"] = tool_payload
    manager.add_message(tool)


def option_texts(widget) -> list[str]:
    """OptionList / SelectOverlay 当前各行的纯文本。"""
    return [widget.get_option_at_index(i).prompt.plain for i in range(widget.option_count)]


@asynccontextmanager
async def chat_app(*, size=None, handle_query=None, **kwargs):
    """创建 ChatApp 并在 run_test 中交给测试，统一 handle_query 默认值。"""
    from core.tui.ui_textual import ChatApp

    app = ChatApp(handle_query=handle_query or (lambda _query: None), **kwargs)
    run_test = app.run_test(size=size) if size is not None else app.run_test()
    async with run_test as pilot:
        yield app, pilot
