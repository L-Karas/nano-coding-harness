"""ModelClient.supports_vision 读取当前模型的注册声明。"""
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
