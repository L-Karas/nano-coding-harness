"""扩展启停：清单原子写回、派发开关、失败回滚与不可切换项。"""
import asyncio
import json

import pytest

import core.extension.dispatcher as dispatcher
import core.extension.loader as loader
from core.extension import ExtensionInfo, list_extensions, toggle_extension
from core.extension.context import BeforeLLMContext
from core.extension.loader import load_extensions

pytestmark = pytest.mark.usefixtures("empty_extension_registry")

REGISTER_LLM = (
    "def register(api):\n"
    "    api.on('before_llm', lambda ctx: ctx.messages.append("
    "{'role': 'user', 'content': 'hit'}))\n"
)
_MODULE = "nano_extension_demo"


def _make_extension(
    root,
    name: str,
    body: str = REGISTER_LLM,
    *,
    enabled=True,
    description=None,
    extra: dict | None = None,
):
    folder = root / name
    folder.mkdir(parents=True)
    payload = {"enabled": enabled}
    if description is not None:
        payload["description"] = description
    if extra:
        payload.update(extra)
    (folder / "extension.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (folder / "__init__.py").write_text(body, encoding="utf-8")
    return folder


def _dispatch_llm() -> BeforeLLMContext:
    ctx = BeforeLLMContext(messages=[], tools=[], max_tokens=1)
    asyncio.run(dispatcher.dispatch("before_llm", ctx))
    return ctx


def test_disabled_extension_registers_but_dispatch_is_skipped(tmp_path):
    _make_extension(tmp_path, "demo", enabled=False)
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("demo", "user", False, "", ("before_llm",)),
    ]
    assert len(dispatcher._HANDLERS["before_llm"]) == 1
    assert dispatcher.is_module_enabled(_MODULE) is False
    assert _dispatch_llm().messages == []


def test_toggle_off_writes_manifest_and_suppresses_dispatch(tmp_path):
    folder = _make_extension(
        tmp_path, "demo", enabled=True, description="Demo",
        extra={"tag": "[demo]", "nested": {"x": 1}},
    )
    load_extensions(tmp_path)

    ok, message, enabled = toggle_extension("demo")

    assert (ok, message, enabled) == (True, "Disabled extension 'demo'", False)
    data = json.loads((folder / "extension.json").read_text(encoding="utf-8"))
    assert data == {
        "enabled": False,
        "description": "Demo",
        "tag": "[demo]",
        "nested": {"x": 1},
    }
    entry = list_extensions()[0]
    assert (entry.enabled, entry.detail, entry.events) == (
        False, "", ("before_llm",))
    assert dispatcher.is_module_enabled(_MODULE) is False
    assert _dispatch_llm().messages == []
    assert list(folder.glob(".*.tmp")) == [], "原子写不应留下临时文件"


def test_toggle_on_restores_dispatch_without_duplicate_registration(tmp_path):
    _make_extension(tmp_path, "demo", enabled=False)
    load_extensions(tmp_path)

    ok, message, enabled = toggle_extension("demo")

    assert (ok, message, enabled) == (True, "Enabled extension 'demo'", True)
    assert list_extensions()[0] == ExtensionInfo(
        "demo", "user", True, "", ("before_llm",))
    assert len(dispatcher._HANDLERS["before_llm"]) == 1, "启停不应重复注册回调"
    assert _dispatch_llm().messages == [{"role": "user", "content": "hit"}]


def test_toggle_load_failed_extension_updates_config_only(tmp_path):
    folder = _make_extension(
        tmp_path, "demo",
        "def register(api):\n    raise RuntimeError('boom')\n",
        enabled=False,
    )
    load_extensions(tmp_path)
    assert list_extensions() == [
        ExtensionInfo("demo", "user", False, "disabled · load failed", ()),
    ]

    ok, message, enabled = toggle_extension("demo")

    assert ok is True and enabled is True
    assert "still fails to load" in message
    assert json.loads((folder / "extension.json").read_text(encoding="utf-8"))["enabled"] is True
    assert list_extensions() == [
        ExtensionInfo("demo", "user", False, "load failed", ()),
    ]
    assert _dispatch_llm().messages == []

    ok, _message, enabled = toggle_extension("demo")
    assert ok is True and enabled is False
    assert list_extensions()[0].detail == "disabled · load failed"


def test_toggle_missing_or_invalid_manifest_is_rejected(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "__init__.py").write_text(REGISTER_LLM, encoding="utf-8")
    bad = _make_extension(tmp_path, "bad")
    (bad / "extension.json").write_text("{not json", encoding="utf-8")
    load_extensions(tmp_path)

    ok, message, _enabled = toggle_extension("missing")
    assert ok is False and "missing extension.json" in message
    ok, message, _enabled = toggle_extension("bad")
    assert ok is False and "invalid extension.json" in message
    assert (bad / "extension.json").read_text(encoding="utf-8") == "{not json"


def test_toggle_unknown_extension_is_rejected():
    ok, message, enabled = toggle_extension("nope")
    assert (ok, enabled) == (False, None)
    assert "restart to load it" in message


def test_manifest_write_failure_keeps_config_and_runtime_state(tmp_path, monkeypatch):
    folder = _make_extension(tmp_path, "demo", enabled=True)
    load_extensions(tmp_path)
    before = (folder / "extension.json").read_text(encoding="utf-8")

    def _fail(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(loader, "_write_manifest", _fail)
    ok, message, enabled = toggle_extension("demo")

    assert ok is False and enabled is None
    assert "Failed to save extension state" in message
    assert (folder / "extension.json").read_text(encoding="utf-8") == before
    assert list_extensions()[0].enabled is True
    assert dispatcher.is_module_enabled(_MODULE) is True
    assert _dispatch_llm().messages == [{"role": "user", "content": "hit"}]


def test_disabling_one_extension_does_not_affect_another(tmp_path):
    _make_extension(tmp_path, "a")
    _make_extension(tmp_path, "b")
    load_extensions(tmp_path)

    toggle_extension("a")

    assert dispatcher.is_module_enabled("nano_extension_a") is False
    assert dispatcher.is_module_enabled("nano_extension_b") is True
    assert [entry.name for entry in list_extensions()] == ["a", "b"]


def test_toggle_missing_enabled_field_writes_true(tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "extension.json").write_text('{"description": "No enabled"}', encoding="utf-8")
    (folder / "__init__.py").write_text(REGISTER_LLM, encoding="utf-8")
    load_extensions(tmp_path)

    assert list_extensions()[0].enabled is False
    ok, _message, enabled = toggle_extension("demo")

    assert ok is True and enabled is True
    data = json.loads((folder / "extension.json").read_text(encoding="utf-8"))
    assert data == {"description": "No enabled", "enabled": True}
    assert list_extensions()[0].enabled is True


def test_manifest_key_order_is_preserved(tmp_path):
    folder = _make_extension(
        tmp_path, "demo", enabled=True, description="Demo", extra={"z": 1})
    load_extensions(tmp_path)

    toggle_extension("demo")

    pairs = json.loads(
        (folder / "extension.json").read_text(encoding="utf-8"),
        object_pairs_hook=list,
    )
    assert [key for key, _value in pairs] == ["enabled", "description", "z"]
