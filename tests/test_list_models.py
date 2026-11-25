"""list_models：只列已配置 provider 的模型，并展示能力 / 档位 / 预算与默认模型。"""
import core.client.registry as registry_mod
import core.tools.extra_tools.model as list_models_mod
from core.config import AgentConfig, AgentConfigManager
from core.tools.extra_tools.model import ListModels


def _config(**kwargs) -> AgentConfigManager:
    return AgentConfigManager(AgentConfig(**kwargs))


def test_lists_only_configured_providers(monkeypatch):
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {"P": {"api_key": "k"}})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {
        "P": {"base_url": "", "model_list": {
            "m": {"vision": True, "thinking": True,
                  "thinking_level_map": {"low": "low", "medium": False, "high": "high"},
                  "context_length": 100_000, "max_output": 2_000},
        }},
        "Q": {"base_url": "", "model_list": {"n": {"context_length": 1}}},
    })
    monkeypatch.setattr(list_models_mod, "CONFIGMANAGER", _config(
        default_model="P:m", default_thinking_level="high",
        default_sub_model=""))
    out = ListModels().run()
    assert "P:m" in out
    assert "Q:n" not in out
    assert "vision=yes" in out
    assert "thinking=yes" in out
    assert "thinking_levels=low,high" in out
    assert "context=100000" in out
    assert "max_output=2000" in out


def test_shows_default_and_sub_default(monkeypatch):
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {"P": {"api_key": "k"}})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {
        "P": {"base_url": "", "model_list": {"main": {}, "sub": {}}},
    })
    monkeypatch.setattr(list_models_mod, "CONFIGMANAGER", _config(
        default_model="P:main", default_thinking_level="medium",
        default_sub_model="P:sub", default_sub_model_thinking_level="low"))
    out = ListModels().run()
    assert "Default model: P:main @ medium" in out
    assert "Default sub model: P:sub @ low" in out


def test_sub_default_falls_back_to_main_model(monkeypatch):
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {})
    monkeypatch.setattr(list_models_mod, "CONFIGMANAGER", _config(
        default_model="P:main", default_thinking_level="high",
        default_sub_model="", default_sub_model_thinking_level="low"))
    out = ListModels().run()
    assert "Default sub model: P:main @ high" in out
    assert "(none: configure a provider API key with /provider first)" in out


def test_thinking_on_without_levels_is_not_confused_with_thinking_off(monkeypatch):
    """思考恒开但不支持调档（map 全 false）必须与 thinking:false 区分开。"""
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {"P": {"api_key": "k"}})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {
        "P": {"base_url": "", "model_list": {
            "always": {"thinking": True, "thinking_level_map": {"low": False, "high": False}},
            "never": {"thinking": False, "thinking_level_map": {"low": "low"}},
        }},
    })
    monkeypatch.setattr(list_models_mod, "CONFIGMANAGER", _config(default_model="P:always"))
    out = ListModels().run()
    assert "P:always | vision=no | thinking=yes | thinking_levels=none (thinking on)" in out
    assert "P:never | vision=no | thinking=no | thinking_levels=none" in out


def test_available_and_management_lists_share_configured_provider_rule(monkeypatch):
    """可用列表只含有 key 的 provider；自定义注销列表保留未配置项，否则无法注销。"""
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {
        "P": {"api_key": "k"},
        "Q": {"api_key": ""},
    })
    monkeypatch.setattr(registry_mod, "_CUSTOM_PROVIDER_AUTH", {"Q": {"base_url": "", "api_key": ""}})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {
        "P": {"base_url": "", "model_list": {"m": {}}},
        "Q": {"base_url": "", "model_list": {"n": {}}},
    })

    assert registry_mod.get_model_list() == [("m", "P")]
    assert registry_mod.get_model_list(custom_model=True) == [("n", "Q")]
    assert registry_mod.get_provider_list() == [("P", True)]
    assert registry_mod.get_provider_list(custom_provider=True) == [("Q", False)]


def test_tool_is_main_only():
    assert ListModels().agent_type == {"main"}


def test_skips_provider_with_empty_api_key(monkeypatch):
    """空 key 与 validate_model_choice 口径一致：不列为可用模型。"""
    monkeypatch.setattr(registry_mod, "_PROVIDER_AUTH", {"P": {"api_key": ""}})
    monkeypatch.setattr(registry_mod, "_MODEL_LIST", {
        "P": {"base_url": "", "model_list": {"m": {}}},
    })
    monkeypatch.setattr(list_models_mod, "CONFIGMANAGER", _config(default_model="P:m"))
    out = ListModels().run()
    assert "  P:m |" not in out
    assert "(none: configure a provider API key with /provider first)" in out
