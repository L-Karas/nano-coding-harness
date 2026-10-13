"""权限规则文件：解析、校验与匹配语义。"""
import json
from pathlib import Path
from unittest.mock import MagicMock

from core.extension.builtin import permission
from core.extension.builtin.permission import (
    external_directory_action,
    load_rules,
    match_terminal,
)


def _write(tmp_path, payload):
    path = tmp_path / ".permissions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_parses_rules_in_file_order_last_match_wins(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"^git": "allow", "force": "deny"}}}))
    assert match_terminal(rules, "git push --force") == ("deny", "force")


def test_missing_file_returns_empty_rules(tmp_path):
    rules = load_rules(tmp_path / "none.json")
    assert rules.terminal == [] and rules.external_directory == {}
    assert match_terminal(rules, "ls") is None


def test_invalid_json_returns_empty_rules_and_logs_error(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    path = tmp_path / ".permissions.json"; path.write_text("{oops", encoding="utf-8")
    assert load_rules(path).terminal == []
    assert logger.error.called


def test_bad_regex_and_bad_action_drop_only_that_entry(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"(": "deny", "^ls": "maybe", "^pwd": "allow"}}}))
    assert [pattern.pattern for pattern, _ in rules.terminal] == ["^pwd"]
    assert logger.error.call_count == 2


def test_matching_is_case_insensitive(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"SUDO": "deny"}}}))
    assert match_terminal(rules, "sudo ls") == ("deny", "SUDO")


def test_external_directory_per_tool(tmp_path):
    rules = load_rules(_write(tmp_path, {
        "read_file": {"external_directory": "allow"},
        "write_file": {"external_directory": "deny"},
        "edit_file": {"external_directory": "ask"},
    }))
    assert external_directory_action(rules, "read_file") == "allow"
    assert external_directory_action(rules, "write_file") == "deny"
    assert external_directory_action(rules, "edit_file") == "ask"
    assert external_directory_action(rules, "grep") is None


def test_unknown_tool_and_unknown_field_ignored_with_warn(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"grep": {"external_directory": "allow"},
                                         "terminal": {"unknown_field": 1, "command": {"^ls": "allow"}}}))
    assert external_directory_action(rules, "grep") is None
    assert [pattern.pattern for pattern, _ in rules.terminal] == ["^ls"]
    assert logger.warning.called


def test_non_dict_top_level_returns_empty_rules_and_logs_error(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, ["terminal"]))
    assert rules.terminal == [] and rules.external_directory == {}
    assert logger.error.called


def test_empty_known_sections_warn_zero_rules(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"terminal": {}, "read_file": {}}))
    assert rules.terminal == [] and rules.external_directory == {}
    messages = [call.args[0] for call in logger.warning.call_args_list]
    assert any("terminal" in message and "未产出" in message for message in messages)
    assert any("read_file" in message and "未产出" in message for message in messages)


def test_terminal_without_command_warns(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    assert load_rules(_write(tmp_path, {"terminal": {"command": None}})).terminal == []
    messages = [call.args[0] for call in logger.warning.call_args_list]
    assert any("terminal" in message and "未产出" in message for message in messages)


def test_read_failure_returns_empty_rules_and_logs_read_error(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    path = tmp_path / ".permissions.json"; path.write_text("{}", encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert load_rules(path).terminal == []
    message = logger.error.call_args.args[0]
    assert "读取失败" in message and "PermissionError" in message and "denied" in message


def test_json_parse_error_message_carries_exception_type(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    path = tmp_path / ".permissions.json"; path.write_text("{oops", encoding="utf-8")
    assert load_rules(path).terminal == []
    message = logger.error.call_args.args[0]
    assert "JSON" in message and "JSONDecodeError" in message


def test_invalid_section_types_drop_section_with_error(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {
        "terminal": {"command": "not-a-dict"},
        "read_file": "allow",
        "write_file": {"external_directory": "deny"},
    }))
    assert rules.terminal == []
    assert external_directory_action(rules, "read_file") is None
    assert external_directory_action(rules, "write_file") == "deny"
    assert logger.error.call_count == 2
