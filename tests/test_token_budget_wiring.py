"""模型调用点的 token 预算接线：单次 max_tokens 必须经当前 client 钳制到该模型有效最大输出。"""
import asyncio
from types import SimpleNamespace

import core.agent_step as step_mod
import core.sub_agent as sa
import core.tools
from core.agent_step import StepPolicy, run_agent_step
from core.runtime_context import ToolContext
from core.tools import ToolPool


def _stream(monkeypatch, response=("done", "", [], "stop", None)):
    async def fake(stream, on_text=None):
        return response

    monkeypatch.setattr(step_mod, "streaming_message", fake)


def test_step_clamps_max_tokens_to_current_model(monkeypatch):
    captured = {}

    class _Client:
        current_provider = "test"

        def clamp_max_tokens(self, requested):
            return min(requested, 8_000)

        def get_model_client(self, async_client=False):
            async def create(**kwargs):  # async client 的 create 与真实 AsyncOpenAI 一样可 await
                captured.update(kwargs)
                return object()
            return create

    _stream(monkeypatch)
    asyncio.run(run_agent_step([], "sys", client=_Client(), pool=ToolPool({}), tctx=ToolContext(),
                               max_tokens=24_000,
                               policy=StepPolicy(use_extensions=False, permission_hooks=False)))
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

    async def identity(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _Client())
    monkeypatch.setattr(sa, "prepare_messages", identity)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "CONFIGMANAGER", SimpleNamespace(
        config=SimpleNamespace(default_max_tokens=12_000)))
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    _stream(monkeypatch)

    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert captured["requested"] == 12_000
    assert captured["max_tokens"] == 7_777
