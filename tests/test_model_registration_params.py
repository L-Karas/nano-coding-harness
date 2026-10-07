"""自定义模型注册：context_length / max_output 必须随 login_model 落盘，且 context_length 能被 load_context_length 读回。"""
import json

import core.client.model as model


def test_login_model_saves_context_length_and_max_output(tmp_path, monkeypatch):
    custom_file = tmp_path / ".custom_models.json"
    monkeypatch.setattr(model, "CUSTOM_MODEL_FILE", custom_file)
    monkeypatch.setitem(model._CUSTOM_PROVIDER_AUTH, "smoke-provider",
                        {"base_url": "", "api_key": ""})
    monkeypatch.setitem(model._PROVIDER_AUTH, "smoke-provider", {"api_key": ""})
    monkeypatch.setitem(model._MODEL_LIST, "smoke-provider", {"base_url": "", "model_list": {}})

    assert model.login_model("smoke-provider", "smoke-model",
                             context_length=128000, max_output=32000)

    stored = json.loads(custom_file.read_text(encoding="utf-8"))
    assert stored["smoke-provider"]["model_list"]["smoke-model"]["context_length"] == 128000
    assert stored["smoke-provider"]["model_list"]["smoke-model"]["max_output"] == 32000

    client = model.ModelClient.__new__(model.ModelClient)  # 不建真实 client
    client.current_provider = "smoke-provider"
    client.current_model = "smoke-model"
    assert client.load_context_length() == 128000


def test_login_model_rejects_non_positive_context_length(tmp_path, monkeypatch):
    """注册表 context_length 是压缩/预算的唯一真源，非正值必须拒绝注册。"""
    custom_file = tmp_path / ".custom_models.json"
    monkeypatch.setattr(model, "CUSTOM_MODEL_FILE", custom_file)
    monkeypatch.setitem(model._CUSTOM_PROVIDER_AUTH, "smoke-provider",
                        {"base_url": "", "api_key": ""})
    monkeypatch.setitem(model._MODEL_LIST, "smoke-provider", {"base_url": "", "model_list": {}})

    assert model.login_model("smoke-provider", "no-context", context_length=0) is False
    assert "no-context" not in model._MODEL_LIST["smoke-provider"]["model_list"]


def test_login_provider_rejects_builtin_name(tmp_path, monkeypatch):
    """回归：未配置的内置 provider 也属重名，不得被自定义 provider 覆盖注册。"""
    monkeypatch.setattr(model, "CUSTOM_PROVIDER_FILE", tmp_path / ".custom_providers.json")
    monkeypatch.setattr(model, "CUSTOM_MODEL_FILE", tmp_path / ".custom_models.json")
    monkeypatch.setitem(model._MODEL_LIST, "Deepseek", {"base_url": "", "model_list": {}})
    monkeypatch.delitem(model._CUSTOM_PROVIDER_AUTH, "Deepseek", raising=False)

    assert model.login_provider("Deepseek", "http://example.com/v1") is False
    assert "Deepseek" not in model._CUSTOM_PROVIDER_AUTH


def test_logout_provider_without_auth_key(tmp_path, monkeypatch):
    """回归：注销尚未配置 API key 的自定义 provider 必须整体成功（曾 KeyError 半途中断）。"""
    monkeypatch.setattr(model, "CUSTOM_PROVIDER_FILE", tmp_path / ".custom_providers.json")
    monkeypatch.setattr(model, "CUSTOM_MODEL_FILE", tmp_path / ".custom_models.json")
    monkeypatch.setitem(model._CUSTOM_PROVIDER_AUTH, "smoke-provider",
                        {"base_url": "http://example.com/v1", "api_key": ""})
    monkeypatch.setitem(model._MODEL_LIST, "smoke-provider",
                        {"base_url": "http://example.com/v1", "model_list": {}})
    monkeypatch.delitem(model._PROVIDER_AUTH, "smoke-provider", raising=False)

    assert model.logout_provider("smoke-provider") is True
    assert "smoke-provider" not in model._CUSTOM_PROVIDER_AUTH
    assert "smoke-provider" not in model._MODEL_LIST
    assert "smoke-provider" not in json.loads(
        (tmp_path / ".custom_providers.json").read_text(encoding="utf-8"))
    assert "smoke-provider" not in json.loads(
        (tmp_path / ".custom_models.json").read_text(encoding="utf-8"))
