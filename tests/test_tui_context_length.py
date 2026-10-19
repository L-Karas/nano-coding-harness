"""footer 上下文长度取当前模型有效值（注册表缺失时回退配置），而不是注册表原始值。"""
from types import SimpleNamespace

import core.client
import core.tui.utils as utils
from core.tui.footer import _token_bar_text


def test_current_context_length_uses_registry_value(monkeypatch):
    monkeypatch.setattr(core.client, "shared_model_client",
                        lambda: SimpleNamespace(load_context_length=lambda: 262_144))
    assert utils.current_context_length() == 262_144


def test_current_context_length_returns_zero_on_failure(monkeypatch):
    def boom():
        raise RuntimeError("no client")

    monkeypatch.setattr(core.client, "shared_model_client", boom)
    assert utils.current_context_length() == 0


def test_token_bar_text_has_context_usage_label():
    assert _token_bar_text(0, 200_000).plain.startswith("Context Usage: [")
    assert _token_bar_text(0, 0).plain.startswith("Context Usage: [")
