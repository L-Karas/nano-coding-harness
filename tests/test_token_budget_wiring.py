"""模型调用点的 token 预算接线：单次 max_tokens 必须经当前 client 钳制到该模型有效最大输出。"""
import asyncio
from types import SimpleNamespace

import core.agent_step as step_mod
import core.sub_agent as sa
import core.tools
from core.agent_step import StepPolicy, run_agent_step
from core.runtime_context import ToolContext
from core.tools import ToolPool
from tests.support import StubModelClient, stream_replies


def _stream(monkeypatch, response=("done", "", [], "stop", None)):
    monkeypatch.setattr(step_mod, "streaming_message", stream_replies([response]))


def test_step_clamps_max_tokens_to_current_model(monkeypatch):
    client = StubModelClient(max_output=8_000)
    _stream(monkeypatch)
    asyncio.run(run_agent_step(
        [],
        "sys",
        client=client,
        pool=ToolPool({}),
        tctx=ToolContext(),
        max_tokens=24_000,
        policy=StepPolicy(use_extensions=False),
    ))
    assert client.calls[0]["max_tokens"] == 8_000


def test_subagent_clamps_max_tokens_to_sub_model(monkeypatch):
    client = StubModelClient(clamp_to=7_777)

    async def identity(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: client)
    monkeypatch.setattr(sa, "prepare_messages", identity)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "CONFIGMANAGER", SimpleNamespace(
        config=SimpleNamespace(default_max_tokens=12_000, enable_experimental_tools=False)))
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    _stream(monkeypatch)

    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert client.requested == 12_000
    assert client.calls[0]["max_tokens"] == 7_777
