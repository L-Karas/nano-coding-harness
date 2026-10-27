"""加载器：清单门槛、原子注册、失败隔离、包内相对导入、幂等。"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.extension.dispatcher as dispatcher
import core.extension.loader as loader
from core.extension import ExtensionInfo, list_extensions
from core.extension.loader import load_extensions
pytestmark = pytest.mark.usefixtures("empty_extension_registry")

REGISTER_LLM = "def register(api):\n    api.on('before_llm', lambda ctx: None)\n"
_MISSING = object()


def _make_extension(
    root: Path,
    name: str,
    body: str = "",
    *,
    enabled=True,
    manifest=True,
    entry=True,
    description=_MISSING,
):
    folder = root / name
    folder.mkdir(parents=True)
    if manifest:
        payload = {"enabled": enabled}
        if description is not _MISSING:
            payload["description"] = description
        (folder / "extension.json").write_text(json.dumps(payload), encoding="utf-8")
    if entry:
        (folder / "__init__.py").write_text(body, encoding="utf-8")
    return folder


def _count(event):
    return len(dispatcher._HANDLERS.get(event, ()))


def test_enabled_extension_registers(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1


def test_load_success_logged_with_name_and_count(tmp_path, monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(loader, "_LOGGER", logger)
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    assert "[Extension] 加载成功: demo（注册 1 个回调）" in [c.args[0] for c in logger.info.call_args_list]


def test_register_failure_does_not_log_success(tmp_path, monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(loader, "_LOGGER", logger)
    _make_extension(tmp_path, "half",
                    "def register(api):\n    api.on('before_llm', lambda ctx: None)\n    raise RuntimeError('late')\n")
    load_extensions(tmp_path)
    assert all("加载成功" not in c.args[0] for c in logger.info.call_args_list)


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_not_exactly_true_is_disabled(tmp_path, value):
    _make_extension(
        tmp_path,
        "demo",
        REGISTER_LLM,
        enabled=value,
    )
    load_extensions(tmp_path)
    assert _count("before_llm") == 0


def test_missing_or_invalid_manifest_isolated(tmp_path):
    _make_extension(
        tmp_path,
        "no_manifest",
        REGISTER_LLM,
        manifest=False,
    )
    bad = _make_extension(tmp_path, "bad", REGISTER_LLM)
    (bad / "extension.json").write_text("{not json", encoding="utf-8")
    _make_extension(tmp_path, "good", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1  # 只有 good 生效


def test_import_error_isolated(tmp_path):
    _make_extension(tmp_path, "broken", "raise RuntimeError('import boom')\n")
    _make_extension(tmp_path, "good", "def register(api):\n    api.on('before_tool', lambda ctx: None)\n")
    load_extensions(tmp_path)
    assert _count("before_tool") == 1


def test_missing_entry_and_missing_register_skipped(tmp_path):
    _make_extension(tmp_path, "no_entry", entry=False)
    _make_extension(tmp_path, "no_register", "X = 1\n")
    load_extensions(tmp_path)
    assert not any(dispatcher._HANDLERS.values())


def test_register_failure_registers_nothing(tmp_path):
    _make_extension(tmp_path, "half",
                    "def register(api):\n    api.on('before_llm', lambda ctx: None)\n    raise RuntimeError('late')\n")
    load_extensions(tmp_path)
    assert _count("before_llm") == 0  # 原子性：不允许半个模块生效


def test_package_relative_import(tmp_path):
    folder = _make_extension(tmp_path, "pkg", "from .helper import VALUE\n\n" + REGISTER_LLM)
    (folder / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    load_extensions(tmp_path)
    assert _count("before_llm") == 1


def test_underscore_folder_skipped(tmp_path):
    _make_extension(tmp_path, "_hidden", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 0


def test_external_extension_handlers_use_default_stage_metadata(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    (handler,) = dispatcher._HANDLERS["before_llm"]
    assert handler.stage == "user"
    assert handler.fail_closed is False
    assert handler.builtin is False


def test_load_is_idempotent(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1


def test_example_extension_loads(tmp_path):
    import shutil
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "examples" / "extension_example"
    shutil.copytree(source, tmp_path / "extension_example")
    load_extensions(tmp_path)
    assert _count("before_llm") >= 1


def test_enabled_extension_recorded_with_events(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("demo", "user", True, "", ("before_llm",)),
    ]


def test_description_is_recorded_and_whitespace_collapsed(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM,
                    description="  Marks\n\tthings  ")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("demo", "user", True, "", ("before_llm",), "Marks things"),
    ]


def test_disabled_extension_keeps_description(tmp_path):
    _make_extension(tmp_path, "off", REGISTER_LLM, enabled=False,
                    description="Shown while disabled")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("off", "user", False, "disabled", (), "Shown while disabled"),
    ]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ("X = 1\n", "missing register(api)"),
        ("def register(api):\n    raise RuntimeError('boom')\n", "load failed"),
    ],
)
def test_load_failure_keeps_description(tmp_path, body, detail):
    _make_extension(tmp_path, "broken", body, description="Broken but described")
    load_extensions(tmp_path)

    entry = list_extensions()[0]
    assert (entry.enabled, entry.detail, entry.description) == (
        False, detail, "Broken but described")


@pytest.mark.parametrize("value", [None, "", "   \n\t", 123, True, [], {}])
def test_invalid_or_empty_description_is_ignored(tmp_path, value):
    _make_extension(tmp_path, "demo", REGISTER_LLM, description=value)
    load_extensions(tmp_path)

    entry = list_extensions()[0]
    assert entry.enabled is True
    assert entry.description == ""


def test_disabled_extension_recorded_with_reason(tmp_path):
    _make_extension(tmp_path, "off", REGISTER_LLM, enabled=False)
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("off", "user", False, "disabled", ()),
    ]


def test_missing_and_invalid_manifest_recorded(tmp_path):
    _make_extension(tmp_path, "missing", REGISTER_LLM, manifest=False)
    bad = _make_extension(tmp_path, "bad", REGISTER_LLM)
    (bad / "extension.json").write_text("{not json", encoding="utf-8")
    not_object = _make_extension(tmp_path, "not_object", REGISTER_LLM)
    (not_object / "extension.json").write_text("[]", encoding="utf-8")
    load_extensions(tmp_path)

    assert [(e.name, e.enabled, e.detail) for e in list_extensions()] == [
        ("bad", False, "invalid extension.json"),
        ("missing", False, "missing extension.json"),
        ("not_object", False, "invalid extension.json"),
    ]
    assert all(e.description == "" for e in list_extensions())


def test_missing_register_recorded(tmp_path):
    _make_extension(tmp_path, "no_register", "X = 1\n")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("no_register", "user", False, "missing register(api)", ()),
    ]


def test_register_failure_recorded_as_load_failed(tmp_path):
    _make_extension(tmp_path, "half",
                    "def register(api):\n    api.on('before_llm', lambda ctx: None)\n    raise RuntimeError('late')\n")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("half", "user", False, "load failed", ()),
    ]


def test_underscore_folder_not_recorded(tmp_path):
    _make_extension(tmp_path, "_hidden", REGISTER_LLM)
    load_extensions(tmp_path)

    assert list_extensions() == []


def test_load_twice_records_once(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    load_extensions(tmp_path)

    assert [e.name for e in list_extensions()] == ["demo"]


def test_events_keep_registration_order_and_dedupe(tmp_path):
    _make_extension(tmp_path, "multi",
                    "def register(api):\n"
                    "    api.on('after_tool', lambda ctx: None)\n"
                    "    api.on('before_llm', lambda ctx: None)\n"
                    "    api.on('after_tool', lambda ctx: None)\n")
    load_extensions(tmp_path)

    assert list_extensions()[0].events == ("after_tool", "before_llm")
