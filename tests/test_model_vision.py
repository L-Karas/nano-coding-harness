"""ModelClient.supports_vision 读取当前模型的注册声明；无该查询的测试替身按支持处理。"""
from types import SimpleNamespace

import core.client.model as model


def test_supports_vision_reads_current_model(monkeypatch):
    monkeypatch.setitem(model._MODEL_LIST, "P", {
        "model_list": {
            "v": {"vision": True},
            "novision": {"vision": False},
        },
    })
    client = model.ModelClient.__new__(model.ModelClient)
    client.current_provider = "P"
    client.current_model = "v"
    assert client.supports_vision() is True
    client.current_model = "novision"
    assert client.supports_vision() is False
    client.current_model = ""
    assert client.supports_vision() is False


def test_model_supports_vision_uses_the_client_query_when_present():
    assert model.model_supports_vision(SimpleNamespace(supports_vision=lambda: True)) is True
    assert model.model_supports_vision(SimpleNamespace(supports_vision=lambda: False)) is False
    assert model.model_supports_vision(SimpleNamespace(supports_vision=False)) is False


def test_model_supports_vision_defaults_to_true_for_clients_without_the_query():
    assert model.model_supports_vision(object()) is True
