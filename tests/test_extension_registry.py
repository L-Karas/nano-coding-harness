"""扩展登记表：来源排序、事件去重、同名同源替换、reset 清理。"""
import pytest

import core.extension.registry as registry
from core.extension import ExtensionInfo, list_extensions, reset_extensions


@pytest.fixture(autouse=True)
def _clean():
    registry.reset()
    yield
    registry.reset()


def _record_builtins_and_users():
    registry.record("zeta", "user", enabled=True, events=["before_llm"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="disabled")
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])


def test_list_puts_builtins_first_then_users_by_name():
    _record_builtins_and_users()

    assert list_extensions() == [
        ExtensionInfo("observers", "builtin", True, "", ("before_tool",)),
        ExtensionInfo("permission", "builtin", True, "", ("before_tool",)),
        ExtensionInfo("alpha", "user", False, "disabled", ()),
        ExtensionInfo("zeta", "user", True, "", ("before_llm",)),
    ]


def test_events_are_deduplicated_in_registration_order():
    registry.record("demo", "user", enabled=True,
                    events=["after_tool", "before_llm", "after_tool"])

    assert list_extensions()[0].events == ("after_tool", "before_llm")


def test_same_origin_and_name_replaces_in_place():
    registry.record("demo", "user", enabled=False, detail="disabled")
    registry.record("demo", "user", enabled=True, events=["before_llm"])

    assert list_extensions() == [
        ExtensionInfo("demo", "user", True, "", ("before_llm",)),
    ]


def test_same_name_keeps_user_and_builtin_separate():
    registry.record("permission", "user", enabled=True)
    registry.record("permission", "builtin", enabled=True)
    registry.record("permission", "user", enabled=False, detail="load failed")

    assert [(e.origin, e.enabled, e.detail) for e in list_extensions()] == [
        ("builtin", True, ""),
        ("user", False, "load failed"),
    ]


def test_reset_clears_all_entries():
    registry.record("demo", "user", enabled=True)
    reset_extensions()

    assert list_extensions() == []
