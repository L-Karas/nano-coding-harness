"""内置扩展注册入口：观察者先于权限门登记，内置回调不受 include_external 影响。"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

import core.extension.builtin as builtin_mod
import core.extension.dispatcher as dispatcher
from core.extension.builtin import observers, register_builtin_extensions
from core.extension.builtin.permission import Rules
from core.extension.context import AfterToolContext, BeforeToolContext


@pytest.fixture(autouse=True)
def _clean():
    dispatcher.reset(); yield; dispatcher.reset()


def test_builtins_register_observers_before_gate():
    register_builtin_extensions(rules=Rules())
    stages = [(h.module_name, h.stage) for h in dispatcher._HANDLERS["before_tool"]]
    assert stages == [("builtin:observers", "observer"), ("builtin:permission", "gate")]
    gate = dispatcher._HANDLERS["before_tool"][-1]
    assert gate.fail_closed is True and gate.builtin is True
    assert all(handler.builtin is True for handler in dispatcher._HANDLERS["before_tool"])


def test_register_without_rules_loads_permission_file(tmp_path, monkeypatch):
    rules_file = tmp_path / ".permissions.json"
    rules_file.write_text(json.dumps({"terminal": {"command": {"^curl": "deny"}}}),
                          encoding="utf-8")
    monkeypatch.setattr(builtin_mod, "PERMISSION_FILE", rules_file)
    register_builtin_extensions()
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "curl http://example.com"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    assert ctx.block_reason == "Permission denied by rule: ^curl"


def test_tool_call_is_logged_even_when_floor_blocks(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    assert ctx.blocked
    assert logger.info.called


def test_tool_call_log_text_keeps_legacy_format(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    logger.info.assert_called_once_with(
        '[HOOK] call tool terminal with arguments: {"command": "ls"}')


def test_large_output_warns(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = AfterToolContext(tool_name="terminal", args={}, result="x" * 100_001)
    asyncio.run(dispatcher.dispatch("after_tool", ctx))
    assert logger.warning.called


def test_large_output_threshold_boundary(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    at_limit = AfterToolContext(tool_name="terminal", args={}, result="x" * 100_000)
    asyncio.run(dispatcher.dispatch("after_tool", at_limit))
    assert not logger.warning.called
    over_limit = AfterToolContext(tool_name="terminal", args={}, result="x" * 100_001)
    asyncio.run(dispatcher.dispatch("after_tool", over_limit))
    assert logger.warning.called


def test_large_output_warning_text_keeps_legacy_format(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = AfterToolContext(tool_name="terminal", args={}, result="x" * 100_001)
    asyncio.run(dispatcher.dispatch("after_tool", ctx))
    logger.warning.assert_called_once_with("[HOOK] large output from terminal: 100001 chars")


def test_use_extensions_false_still_enforces_floor():
    register_builtin_extensions(rules=Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx, include_external=False))
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."
