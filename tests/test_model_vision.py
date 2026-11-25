"""ModelClient.supports_vision 读取当前模型的注册声明；无该查询的测试替身按支持处理。"""
from types import SimpleNamespace

import core.client.model_client as client_mod
import core.client.registry as registry_mod


def test_supports_vision_reads_current_model(monkeypatch):
    monkeypatch.setitem(registry_mod._MODEL_LIST, "P", {
        "model_list": {
            "v": {"vision": True},
            "novision": {"vision": False},
        },
    })
    client = client_mod.ModelClient.__new__(client_mod.ModelClient)
    client.current_provider = "P"
    client.current_model = "v"
    assert client.supports_vision() is True
    client.current_model = "novision"
    assert client.supports_vision() is False
    client.current_model = ""
    assert client.supports_vision() is False


def test_model_supports_vision_uses_the_client_query_when_present():
    assert client_mod.model_supports_vision(SimpleNamespace(supports_vision=lambda: True)) is True
    assert client_mod.model_supports_vision(SimpleNamespace(supports_vision=lambda: False)) is False
    assert client_mod.model_supports_vision(SimpleNamespace(supports_vision=False)) is False


def test_model_supports_vision_defaults_to_true_for_clients_without_the_query():
    assert client_mod.model_supports_vision(object()) is True
