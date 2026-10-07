"""模型调用点的 token 预算接线：单次 max_tokens 必须经当前 client 钳制到该模型有效最大输出。"""
import asyncio
import contextlib
from types import SimpleNamespace

import core.loop_with_interrupt as lwi
import core.sub_agent as sa
import core.tools


def test_call_llm_clamps_max_tokens_to_current_model(monkeypatch):
    captured = {}

    class _Client:
        def clamp_max_tokens(self, requested):
            return min(requested, 8_000)

        def get_model_client(self, async_client=False):
            async def create(**kwargs):  # async client 的 create 与真实 AsyncOpenAI 一样可 await
                captured.update(kwargs)
                return "stream"
            return create

    async def _with_retry_async(operation):
        return await operation()

    monkeypatch.setattr(lwi, "shared_model_client", lambda: _Client())
    monkeypatch.setattr(lwi, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(lwi, "with_retry_async", _with_retry_async)
    monkeypatch.setattr(lwi, "render_working_status", contextlib.nullcontext)

    runtime = lwi.AgentRuntime.__new__(lwi.AgentRuntime)  # 不启动线程 / 事件循环
    asyncio.run(runtime.call_llm(messages=[], tools=[], max_tokens=24_000, ctx=None))

    assert captured["max_tokens"] == 8_000


def test_subagent_clamps_max_tokens_to_sub_model(monkeypatch):
    captured = {}

    class _Client:
        current_provider = "test"

        def clamp_max_tokens(self, requested):
            captured["requested"] = requested
            return 7_777

        def get_model_client(self, **_):
            async def create(**kwargs):
                captured.update(kwargs)
                return object()
            return create

    async def fake_stream(stream, ctx=None):
        return "done", "", [], "stop", None

    async def identity(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _Client())
    monkeypatch.setattr(sa, "prepare_messages", identity)
    monkeypatch.setattr(sa, "streaming_message", fake_stream)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "CONFIGMANAGER", SimpleNamespace(
        config=SimpleNamespace(default_max_tokens=12_000)))
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: ([], {}))

    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert captured["requested"] == 12_000
    assert captured["max_tokens"] == 7_777
