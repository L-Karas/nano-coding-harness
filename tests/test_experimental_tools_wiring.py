"""功能开关接线：enable_experimental_tools 必须传到主 / 子代理的工具池装配。

装配侧的 experimental 过滤由 `tests/test_tool_registry.py` 覆盖；这里只盯「配置 → 装配」这一跳：
两个调用点都从 CONFIGMANAGER 实时读取，开关拨到开时装配函数必须收到 True。
"""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import core.agent_step as step_mod
import core.interaction as interaction
import core.loop_with_interrupt as lwi
import core.sub_agent as sa
import core.tools as tools_mod
from core.tools import ToolPool


class _Mgr:
    def __init__(self):
        self.messages = [{"role": "user", "content": "hi"}]

    def load_messages(self):
        return list(self.messages)

    def update_messages(self, messages):
        self.messages = list(messages)

    def add_message(self, message):
        self.messages.append(message)


class _Client:
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, **_):
        async def create(**kwargs):
            return object()
        return create


def _config_with(monkeypatch, module, **overrides):
    """真配置的副本 + 指定字段改写：不动进程内单例，其余字段保持真实。"""
    config = module.CONFIGMANAGER.config.model_copy(update=overrides)
    monkeypatch.setattr(module, "CONFIGMANAGER", SimpleNamespace(config=config))


def _silence_render(monkeypatch):
    for name in ("stream_assistant_response", "render_tool_call", "render_tool_result",
                 "render_tool_result_diff", "render_background_notification",
                 "render_working_status", "render_thinking_status", "render_scope"):
        monkeypatch.setattr(interaction, name, lambda *a, **k: nullcontext())


def _silence_step(monkeypatch):
    async def fake_stream(stream, on_text=None):
        return ("done", "", [], "stop", None)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a, **k: None)
    monkeypatch.setattr(step_mod, "should_run_background", lambda *a: False)


def _capture_pool(seen):
    def fake_pool(agent_type="main", enable_experimental=False, exclude=frozenset()):
        seen.update({"agent_type": agent_type, "enable_experimental": enable_experimental})
        return ToolPool({})

    return fake_pool


def test_main_loop_passes_configured_flag_to_pool(monkeypatch):
    seen = {}
    _silence_render(monkeypatch)
    _silence_step(monkeypatch)
    _config_with(monkeypatch, lwi, enable_experimental_tools=True)
    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", _Mgr())
    monkeypatch.setattr(lwi, "shared_model_client", lambda: _Client())
    monkeypatch.setattr(lwi, "assemble_tool_pool", _capture_pool(seen))

    async def identity(messages, ctx):
        return messages

    monkeypatch.setattr(lwi, "prepare_messages", identity)

    lwi.AgentRuntime().run_turn()

    assert seen == {"agent_type": "main", "enable_experimental": True}


def test_subagent_passes_configured_flag_to_pool(monkeypatch):
    seen = {}
    _silence_step(monkeypatch)
    _config_with(monkeypatch, sa, enable_experimental_tools=True)
    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _Client())
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(tools_mod, "assemble_tool_pool", _capture_pool(seen))

    async def identity(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "prepare_messages", identity)

    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert seen == {"agent_type": "sub-agent", "enable_experimental": True}
