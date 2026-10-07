"""AgentRuntime.stream 的 usage 两种交付姿势都不能崩，且 usage 被记录。

情况 A：usage 挂在最后一个正常 chunk 上（带 choices，如直连 Zhipu GLM）。
情况 B：流末尾追加独立的 usage-only 尾块（choices 为空，如 one-api/new-api 代理）。
情况 C：provider 不返回 completion_tokens_details（None）。
"""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import patch

from openai.types import CompletionUsage
from openai.types.completion_usage import CompletionTokensDetails

from core.loop_with_interrupt import AgentRuntime

EXPECTED = {"prompt_tokens": 11, "completion_tokens": 7, "reasoning_tokens": 3, "total_tokens": 18}


def _chunk(text="", finish_reason=None, usage=None):
    choice = NS(delta=NS(content=text or None, tool_calls=None, reasoning_content=None),
                finish_reason=finish_reason)
    return NS(choices=[choice], usage=usage)


class _FakeStream:
    """stream() 退出时会 await close()，生成器没有异步 close"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self):
        pass


def _usage(details=True):
    return CompletionUsage(
        prompt_tokens=EXPECTED["prompt_tokens"],
        completion_tokens=EXPECTED["completion_tokens"],
        total_tokens=EXPECTED["total_tokens"],
        completion_tokens_details=CompletionTokensDetails(reasoning_tokens=EXPECTED["reasoning_tokens"])
        if details else None,
    )


def _case_a():
    yield _chunk("你好")
    yield _chunk("，世界", finish_reason="stop", usage=_usage())  # usage 与正文同块


def _case_b():
    yield _chunk("你好")
    yield _chunk("，世界", finish_reason="stop")
    yield NS(choices=[], usage=_usage())  # 独立 usage-only 尾块


def _case_c():
    yield _chunk("你好", finish_reason="stop", usage=_usage(details=False))


def _stream(chunks):
    runtime = AgentRuntime.__new__(AgentRuntime)  # 跳过 __init__：只测 stream，无需事件循环线程
    with patch("core.loop_with_interrupt.render_thinking_status", nullcontext), \
            patch("core.loop_with_interrupt.render_scope", nullcontext), \
            patch("core.loop_with_interrupt.stream_assistant_response", lambda *a, **k: None):
        return asyncio.run(runtime.stream(_FakeStream(chunks)))


def test_usage_recorded_on_both_delivery_shapes():
    for name, chunks in [("A", _case_a()), ("B", _case_b())]:
        text, _reasoning, tool_calls, finish_reason, usage = _stream(chunks)
        assert text == "你好，世界", (name, text)
        assert tool_calls == [] and finish_reason == "stop", (name, tool_calls, finish_reason)
        assert usage == EXPECTED, (name, usage)


def test_missing_completion_tokens_details_does_not_crash():
    _, _, _, _, usage = _stream(_case_c())
    assert usage == EXPECTED | {"reasoning_tokens": 0}
