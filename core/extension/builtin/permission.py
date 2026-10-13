"""内置权限扩展：.permissions.json 的解析、校验与规则匹配。

规则只覆盖 terminal 与三个文件工具（read_file / write_file / edit_file）；
文件整体非法时不生效并记 ERROR，单条规则非法只丢弃自身，未知键忽略并记 WARN。
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.log.log import get_logger

_LOGGER = get_logger(__name__)

ACTIONS = ("allow", "ask", "deny")
_FILE_TOOLS = ("read_file", "write_file", "edit_file")


@dataclass
class Rules:
    """规则文件解析结果：terminal 按文件顺序保存，命中的后者覆盖前者。"""

    terminal: list[tuple[re.Pattern, str]] = field(default_factory=list)
    external_directory: dict[str, str] = field(default_factory=dict)


def load_rules(path: Path) -> Rules:
    """读取并校验规则文件；文件缺失或整体非法时返回空规则（不抛异常）。"""
    if not path.exists():
        return Rules()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Permission] 规则文件解析失败，忽略全部规则: {path}")
        return Rules()
    if not isinstance(data, dict):
        _LOGGER.error(f"[Permission] 规则文件顶层必须是对象，忽略全部规则: {path}")
        return Rules()
    return _parse_sections(data)


def match_terminal(rules: Rules, command: str) -> tuple[str, str] | None:
    """按文件顺序返回最后一个命中命令的 (action, 正则原文)；无命中返回 None。"""
    matched = None
    for pattern, action in rules.terminal:
        if pattern.search(command):
            matched = (action, pattern.pattern)
    return matched


def external_directory_action(rules: Rules, tool_name: str) -> str | None:
    """返回该文件工具的越界目录策略；未配置或工具未覆盖时返回 None。"""
    return rules.external_directory.get(tool_name)


def _parse_sections(data: dict) -> Rules:
    rules = Rules()
    for tool_name, section in data.items():
        if tool_name == "terminal":
            _parse_terminal_section(section, rules)
        elif tool_name in _FILE_TOOLS:
            _parse_external_directory_section(tool_name, section, rules)
        else:
            _LOGGER.warning(f"[Permission] 未知工具键，忽略: {tool_name}")
    return rules


def _parse_terminal_section(section: object, rules: Rules) -> None:
    if not isinstance(section, dict):
        _LOGGER.error("[Permission] terminal 段必须是对象，忽略该段")
        return
    for key in section:
        if key != "command":
            _LOGGER.warning(f"[Permission] terminal 段未知字段，忽略: {key}")
    commands = section.get("command")
    if commands is None:
        return
    if not isinstance(commands, dict):
        _LOGGER.error("[Permission] terminal.command 必须是对象，忽略该段")
        return
    for pattern_text, action in commands.items():
        if action not in ACTIONS:
            _LOGGER.error(f"[Permission] 非法 action，忽略规则 {pattern_text!r}: {action!r}")
            continue
        try:
            compiled = re.compile(pattern_text, re.IGNORECASE)
        except re.error:
            _LOGGER.error(f"[Permission] 非法正则，忽略规则: {pattern_text!r}")
            continue
        rules.terminal.append((compiled, action))


def _parse_external_directory_section(tool_name: str, section: object, rules: Rules) -> None:
    if not isinstance(section, dict):
        _LOGGER.error(f"[Permission] {tool_name} 段必须是对象，忽略该段")
        return
    for key, action in section.items():
        if key != "external_directory":
            _LOGGER.warning(f"[Permission] {tool_name} 段未知字段，忽略: {key}")
            continue
        if action not in ACTIONS:
            _LOGGER.error(
                f"[Permission] 非法 action，忽略 {tool_name}.external_directory: {action!r}")
            continue
        rules.external_directory[tool_name] = action
