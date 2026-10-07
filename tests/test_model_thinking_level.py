"""模型/思考档位切换不落盘 + unconfigure 清理默认模型配置（provider:model 精确匹配）回归。"""
import json

import pytest

import core.client.model as model
import core.config as config_mod


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    setting_file = tmp_path / ".settings.json"
    auth_file = tmp_path / ".auth.json"
    setting_file.write_text(json.dumps({
        "default_model": "Qwen:qwen3.8-max",
        "default_thinking_level": "max",
    }), encoding="utf-8")
    auth_file.write_text(json.dumps({"Qwen": {"api_key": "test-key"}}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    monkeypatch.setattr(model, "PROVIDER_AUTH_FILE", auth_file)
    monkeypatch.setattr(model, "OpenAI", FakeOpenAI)  # 不建真实连接
    mgr = config_mod.AgentConfigManager.load_config()
    monkeypatch.setattr(model, "CONFIGMANAGER", mgr)
    monkeypatch.setattr(config_mod, "CONFIGMANAGER", mgr)
    model.load_model_registry()
    return setting_file


class FakeCompletions:
    def create(self, **kwargs):
        return kwargs


class FakeChat:
    completions = FakeCompletions()


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.chat = FakeChat()


def _reload_settings() -> None:
    """模拟重启：按当前 .settings.json 重新加载运行时单例。"""
    mgr = config_mod.AgentConfigManager.load_config()
    model.CONFIGMANAGER = mgr
    config_mod.CONFIGMANAGER = mgr


def test_switch_model_and_effort_not_persisted(ctx):
    """切换模型（/model）与思考档位（/effort）只改内存，默认配置仅 /settings 能改。"""
    client = model.ModelClient()
    client.set_model_client("Qwen", "qwen3.8-flash")
    client.set_thinking_level("high")

    assert client.current_provider == "Qwen" and client.current_model == "qwen3.8-flash"
    assert json.loads(ctx.read_text(encoding="utf-8")) == {
        "default_model": "Qwen:qwen3.8-max",
        "default_thinking_level": "max",
    }


def test_default_thinking_level_used_on_start(ctx):
    client = model.ModelClient()
    assert client.current_provider == "Qwen" and client.current_model == "qwen3.8-max"
    assert client.current_thinking_level == "max"


def test_sub_client_uses_sub_model_and_thinking_level(ctx):
    """子代理 client 走 default_sub_model + default_sub_model_thinking_level，与主 client 独立。"""
    ctx.write_text(json.dumps({
        "default_model": "Qwen:qwen3.8-max",
        "default_thinking_level": "max",
        "default_sub_model": "Qwen:qwen3.8-flash",
        "default_sub_model_thinking_level": "low",
    }), encoding="utf-8")
    _reload_settings()

    main = model.ModelClient()
    sub = model.ModelClient(sub_model=True)
    assert (main.current_model, main.current_thinking_level) == ("qwen3.8-max", "max")
    assert (sub.current_model, sub.current_thinking_level) == ("qwen3.8-flash", "low")


def test_sub_client_falls_back_to_default_model_when_unset(ctx):
    """default_sub_model 为空时回退默认模型 + 默认档位（sub 档位此时不生效）。"""
    ctx.write_text(json.dumps({
        "default_model": "Qwen:qwen3.8-max",
        "default_thinking_level": "high",
        "default_sub_model": "",
        "default_sub_model_thinking_level": "low",
    }), encoding="utf-8")
    _reload_settings()

    sub = model.ModelClient(sub_model=True)
    assert (sub.current_model, sub.current_thinking_level) == ("qwen3.8-max", "high")


def test_request_kwargs_mapping(ctx):
    client = model.ModelClient()
    client.set_model_client("Qwen", "qwen3.8-max", "max")

    def req_kwargs():
        return {k: v for k, v in client.get_model_client()().items() if k != "model"}

    assert req_kwargs() == {
        "reasoning_effort": "xhigh",  # UI 键 "max" 在使用时映射成 API 值
        "extra_body": {"thinking": {"type": "enabled"}},
    }

    client.set_thinking_level("minimal")  # Qwen 不支持此档(map=false)：思考照开，不传 effort
    assert client.current_model_thinking is True
    assert req_kwargs() == {"extra_body": {"thinking": {"type": "enabled"}}}

    client.set_thinking_level("")  # 关闭思考
    assert client.current_model_thinking is False
    assert req_kwargs() == {}


def test_thinking_only_model_as_default(ctx):
    # qwen3.7-max: thinking=True 但 map 全 false(只支持开启思考，不支持调级)，作为默认配置必须可用
    ctx.write_text(json.dumps({
        "default_model": "Qwen:qwen3.7-max",
        "default_thinking_level": "max",
    }), encoding="utf-8")
    _reload_settings()

    client = model.ModelClient()
    assert client.current_model_thinking is True
    assert client.current_thinking_level == "max"
    req = {k: v for k, v in client.get_model_client()().items() if k != "model"}
    assert req == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_empty_default_client_unset_then_settable(ctx):
    """回归：.settings.json 默认配置为空时 ModelClient 构造不得 KeyError('')；
    set_model_client 只改内存修复当前 client，默认配置保持空。"""
    ctx.write_text(json.dumps({"default_model": "", "default_thinking_level": "max"}), encoding="utf-8")
    _reload_settings()

    client = model.ModelClient()  # 曾抛 KeyError('')
    assert client.current_provider == "" and client.current_model == ""

    client.set_model_client("Qwen", "qwen3.8-flash")  # /model Enter 路径
    assert client.current_model == "qwen3.8-flash"
    assert json.loads(ctx.read_text(encoding="utf-8"))["default_model"] == ""  # 不落盘


# ---------- unconfigure_provider（/provider Delete 键路径）回归 ----------


def test_unconfigure_default_provider_resets_client_and_files(ctx):
    """回归：删除的恰是当前默认提供商时，auth 移除该 provider、默认模型配置清空、
    共享 client 复位；default_provider 字段已删除，落盘不含该键。"""
    client = model.shared_model_client()  # 按 ctx 默认配置(Qwen)建好
    assert client.current_provider == "Qwen"

    model.unconfigure_provider("Qwen")

    assert "Qwen" not in json.loads(model.PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
    stored = json.loads(ctx.read_text(encoding="utf-8"))
    assert stored["default_model"] == "" and "default_provider" not in stored
    assert client.current_provider == "" and client.current_client is None, \
        "共享 client 不得继续持有已删 key"
    with pytest.raises(Exception, match="Please set provider and model"):
        client.get_model_client()


def test_unconfigure_non_default_and_repeat_is_noop(ctx):
    """回归：unconfigure 须幂等——删非默认 provider 不动默认模型配置；
    对未配置/已删的 provider 重复调用不得抛错。"""
    model.unconfigure_provider("Deepseek")  # auth 文件里没有 Deepseek：no-op
    assert json.loads(model.PROVIDER_AUTH_FILE.read_text(encoding="utf-8")) == {
        "Qwen": {"api_key": "test-key"}}
    assert json.loads(ctx.read_text(encoding="utf-8"))["default_model"] == "Qwen:qwen3.8-max"

    model.unconfigure_provider("Qwen")  # 删默认：模型配置清空
    assert "Qwen" not in json.loads(model.PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
    model.unconfigure_provider("Qwen")  # 已删：重复调用幂等
    assert json.loads(ctx.read_text(encoding="utf-8"))["default_model"] == ""


def test_unconfigure_settings_without_default_model_key(ctx):
    """回归：.settings.json 缺少默认模型键（从未配置/手改）时删除 provider 不得 KeyError，
    且不触发重写（文件原样保留）。"""
    ctx.write_text(json.dumps({"default_thinking_level": "max"}), encoding="utf-8")
    _reload_settings()

    model.unconfigure_provider("Qwen")

    assert "Qwen" not in json.loads(model.PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
    assert json.loads(ctx.read_text(encoding="utf-8")) == {"default_thinking_level": "max"}


def test_update_default_models_matches_provider_model_exactly(ctx):
    """provider 段不匹配（含旧实现的子串误判）不得清默认配置；模型注销须精确匹配。"""
    assert model._update_default_models("Deep", "") is False  # "Deep" 不是 "Deepseek" 前缀
    assert model._update_default_models("Qwen", "qwen3.8-flash") is False
    assert json.loads(ctx.read_text(encoding="utf-8"))["default_model"] == "Qwen:qwen3.8-max"

    assert model._update_default_models("Qwen", "qwen3.8-max") is True
    assert json.loads(ctx.read_text(encoding="utf-8"))["default_model"] == ""
