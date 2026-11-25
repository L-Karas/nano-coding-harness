"""子代理动态模型：model_choice 必须成对、按模型配置取 client、缺省仍走 settings。

覆盖设计约定（docs/adr/0016-subagent-model-choice.md）：
- 显式模型在工具构造期校验（同步报错，不变成后台通知）；
- client 按 (provider, model, thinking_level) 缓存，多个子代理并发互不覆盖；
- 子代理的压缩与对话使用同一个 client。
"""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import core.agent_step as step_mod
import core.client.model_client as client_mod
import core.client.registry as registry_mod
import core.config as config_mod
import core.sub_agent as sa
import core.tools
import core.tools.extra_tools.subagent as subagent_tool_mod
from core.client import ModelChoice
from core.config import AgentConfig, AgentConfigManager
from core.runtime_state import RuntimeState
from core.tools import ToolPool
from core.tools.extra_tools.subagent import SpawnSubagent
from tests.support import StubModelClient

PROVIDER = "DynamicModelProvider"
MODEL = "dynamic-test-model"
MODEL_REF = f"{PROVIDER}:{MODEL}"


@pytest.fixture
def registry(monkeypatch):
    """注册一个带测试 key 的 provider / 模型，并隔离显式 client 缓存。"""
    monkeypatch.setattr(client_mod, "_MODEL_CLIENT_CACHE", {})
    monkeypatch.setattr(client_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setitem(registry_mod._MODEL_LIST, PROVIDER, {
        "base_url": "",
        "model_list": {
            MODEL: {
                "vision": True,
                "thinking": True,
                "thinking_level_map": {"low": "low", "high": "high"},
                "context_length": 1_000,
                "max_output": 100,
            },
        },
    })
    monkeypatch.setitem(registry_mod._PROVIDER_AUTH, PROVIDER, {"api_key": "test-key"})
    return MODEL_REF


class _FakeAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _ChosenClient(StubModelClient):
    """带模型身份字段的 client 替身（spawn 展示 / 日志读取这些属性）。"""

    current_provider = PROVIDER
    current_model = MODEL
    current_model_thinking = True
    current_thinking_level = "high"


def _choice(level: str = "high") -> ModelChoice:
    return ModelChoice(model=MODEL_REF, thinking_level=level)


def test_schema_requires_model_and_thinking_level_together():
    """model_choice 是可选对象；对象一旦出现，model 与 thinking_level 都必须给。"""
    model_choice = SpawnSubagent.to_openai_tool()["function"]["parameters"]["properties"]["model_choice"]
    object_branch = next(item for item in model_choice["anyOf"] if item.get("type") == "object")
    assert object_branch["required"] == ["model", "thinking_level"]
    assert "case-sensitive" in object_branch["properties"]["model"]["description"]


@pytest.mark.parametrize("partial", [
    {"model": MODEL_REF},
    {"thinking_level": "high"},
])
def test_tool_rejects_partial_model_choice(registry, partial):
    with pytest.raises(ValidationError):
        SpawnSubagent(description="do it", goal="demo", role="reviewer", model_choice=partial)


def test_tool_rejects_unknown_model(registry):
    with pytest.raises(ValidationError, match="unknown model.*for provider"):
        SpawnSubagent(description="do it", goal="demo", role="reviewer",
                      model_choice={"model": f"{PROVIDER}:missing", "thinking_level": "high"})


def test_tool_rejects_provider_without_api_key(registry, monkeypatch):
    monkeypatch.delitem(registry_mod._PROVIDER_AUTH, PROVIDER)
    with pytest.raises(ValidationError, match="no API key"):
        SpawnSubagent(description="do it", goal="demo", role="reviewer",
                      model_choice={"model": MODEL_REF, "thinking_level": "high"})


def test_tool_rejects_provider_with_empty_api_key(registry, monkeypatch):
    """空 key 等同于未配置：不能把错误推迟到后台建 client 时。"""
    monkeypatch.setitem(registry_mod._PROVIDER_AUTH, PROVIDER, {"api_key": ""})
    with pytest.raises(ValidationError, match="no API key"):
        SpawnSubagent(description="do it", goal="demo", role="reviewer",
                      model_choice={"model": MODEL_REF, "thinking_level": "high"})


def test_invalid_choice_fails_in_precheck_not_in_background(registry):
    """无效模型必须走 precheck_call 同步返回工具错误，而不是后台任务通知。"""
    pool = ToolPool({"spawn_subagent": SpawnSubagent})
    result = pool.precheck_call("spawn_subagent", {
        "description": "do it", "goal": "demo", "role": "reviewer",
        "model_choice": {"model": f"{PROVIDER}:missing", "thinking_level": "high"},
    })
    assert result is not None and result.is_error


def test_tool_rejects_bare_model_name(registry):
    with pytest.raises(ValidationError, match="provider:model"):
        SpawnSubagent(description="do it", goal="demo", role="reviewer",
                      model_choice={"model": MODEL, "thinking_level": "high"})


def test_tool_passes_model_choice_to_spawn(monkeypatch, registry):
    captured = {}

    async def fake_spawn(description, ctx=None, goal="", working_directory=None, role="subagent",
                         model_choice=None):
        captured["model_choice"] = model_choice
        return "ok"

    monkeypatch.setattr(subagent_tool_mod, "spawn_subagent", fake_spawn)
    choice = _choice()
    result = asyncio.run(SpawnSubagent(description="do it", goal="demo", role="reviewer",
                                       model_choice=choice).arun(None))
    assert result == "ok"
    assert captured["model_choice"] == choice


def _patch_subagent_run(monkeypatch, state, client):
    """绕开真实模型调用：记录 prepare / step 收到的 client 与右栏登记内容。"""
    seen = {}
    monkeypatch.setattr(sa, "RUNTIME_STATE", state)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(core.tools, "assemble_tool_pool",
                        lambda *a, **k: SimpleNamespace(schemas=lambda: []))

    async def prepare(messages, ctx=None, client=None):
        seen["prepare_client"] = client
        return messages

    async def fake_step(history, system_prompt, **kwargs):
        seen["step_client"] = kwargs["client"]
        seen["entry"] = state.snapshot().subagents[0]
        return SimpleNamespace(aborted=False, assistant_message={"content": "done"},
                               followup_messages=[], tool_calls=[])

    monkeypatch.setattr(sa, "prepare_messages", prepare)
    monkeypatch.setattr(step_mod, "run_agent_step", fake_step)
    return seen


def test_spawn_uses_client_for_model_choice(monkeypatch, registry):
    state = RuntimeState()
    client = _ChosenClient()
    requested = []
    monkeypatch.setattr(sa, "model_client_for", lambda choice: requested.append(choice) or client)
    seen = _patch_subagent_run(monkeypatch, state, client)
    choice = _choice()

    assert asyncio.run(sa.spawn_subagent("explore", model_choice=choice)) == "done"
    assert requested == [choice]
    assert seen["prepare_client"] is client
    assert seen["step_client"] is client
    assert seen["entry"].model == MODEL_REF
    assert seen["entry"].thinking_level == "high"
    assert state.snapshot().subagents == []


def test_spawn_defaults_to_shared_sub_client(monkeypatch):
    state = RuntimeState()
    client = _ChosenClient()
    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: client)

    def unexpected(_choice):
        raise AssertionError("未给 model_choice 时不得走显式模型工厂")

    monkeypatch.setattr(sa, "model_client_for", unexpected)
    seen = _patch_subagent_run(monkeypatch, state, client)

    assert asyncio.run(sa.spawn_subagent("explore")) == "done"
    assert seen["prepare_client"] is client
    assert seen["entry"].model == MODEL_REF


def test_thinking_display_marks_thinking_disabled_model_as_off():
    """thinking: false 时即使档位有值也不能展示成开启。"""
    assert sa._thinking_display(SimpleNamespace(
        current_model_thinking=False, current_thinking_level="high")) == "off"
    assert sa._thinking_display(SimpleNamespace(
        current_model_thinking=True, current_thinking_level="")) == "enabled (no effort)"


def test_model_client_for_caches_by_model_config(registry):
    first = client_mod.model_client_for(_choice("high"))
    again = client_mod.model_client_for(_choice("high"))
    other_level = client_mod.model_client_for(_choice("low"))

    assert first is again
    assert first is not other_level
    assert (first.current_provider, first.current_model, first.current_thinking_level) == (
        PROVIDER, MODEL, "high")
    assert (other_level.current_provider, other_level.current_model, other_level.current_thinking_level) == (
        PROVIDER, MODEL, "low")


def test_model_client_for_rejects_unknown_model(registry):
    with pytest.raises(ValueError, match="unknown model"):
        client_mod.model_client_for(ModelChoice(model=f"{PROVIDER}:missing", thinking_level="low"))


def test_refresh_shared_clients_clears_explicit_cache(monkeypatch, registry):
    monkeypatch.setattr(client_mod, "CONFIGMANAGER", AgentConfigManager(AgentConfig()))
    monkeypatch.setattr(client_mod, "_CLIENT", None)  # 不污染其它用例的进程级共享单例
    monkeypatch.setattr(client_mod, "_SUB_CLIENT", None)
    first = client_mod.model_client_for(_choice())
    client_mod._refresh_shared_clients()
    second = client_mod.model_client_for(_choice())
    assert second is not first


def test_configure_provider_clears_cached_explicit_client(tmp_path, monkeypatch, registry):
    """拆分后的跨模块失效接线：换 key 后不得复用旧 client。"""
    monkeypatch.setattr(config_mod.PATHS, "PROVIDER_AUTH_FILE", tmp_path / ".auth.json")
    first = client_mod.model_client_for(_choice())
    registry_mod.configure_provider(PROVIDER, "new-key")
    second = client_mod.model_client_for(_choice())
    assert second is not first


def test_login_model_clears_cached_explicit_client(tmp_path, monkeypatch):
    """同名模型重新注册（thinking 开关变化）后不得复用旧 client。"""
    monkeypatch.setattr(config_mod.PATHS, "CUSTOM_MODEL_FILE", tmp_path / ".custom_models.json")
    monkeypatch.setitem(
        registry_mod._CUSTOM_PROVIDER_AUTH, PROVIDER, {"base_url": "", "api_key": "k"})
    monkeypatch.setitem(registry_mod._MODEL_LIST, PROVIDER, {"base_url": "", "model_list": {}})
    monkeypatch.setitem(registry_mod._PROVIDER_AUTH, PROVIDER, {"api_key": "k"})
    monkeypatch.setattr(client_mod, "_MODEL_CLIENT_CACHE", {})
    monkeypatch.setattr(client_mod, "AsyncOpenAI", _FakeAsyncOpenAI)

    assert registry_mod.login_model(PROVIDER, MODEL, thinking=False, context_length=1_000)
    first = client_mod.model_client_for(_choice())
    assert registry_mod.login_model(PROVIDER, MODEL, thinking=True, context_length=2_000)
    second = client_mod.model_client_for(_choice())
    assert second is not first
