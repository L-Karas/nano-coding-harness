"""
Skills Module
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from core.config import GLOBAL_SKILLS_DIR, PATHS, PROJECT_SKILL_DIR
from core.shared.logger import get_logger
from core.template import SKILL_MESSAGE_TEMPLATE

_LOGGER = get_logger(__name__)

SkillType = Literal["global", "user", "project"]


def _skill_sources() -> tuple[tuple[SkillType, Path], ...]:
    """扫描来源：顺序即优先级（靠后覆盖靠前），project > user > global。

    user 来源随 mode 变化，所以在扫描时从 PATHS 现取，不固化成模块级常量。

    Returns:
        (来源类型, 目录) 列表，顺序即优先级。
    """
    return (("global", GLOBAL_SKILLS_DIR), ("user", PATHS.SKILL_DIR),
            ("project", PROJECT_SKILL_DIR))


SKILL_REGISTRY: dict[str, "Skill"] = {}

# /skills 的启用状态：只记被禁用的名字（缺省即启用）。文件缺失按空状态处理；
# 文件损坏时 fail-open（全部启用），错误原因由 skill_state_error() 供窗口展示。
_DISABLED: set[str] = set()
_STATE_ERROR: str | None = None


@dataclass
class Skill:
    """Skill 类，用于存储 skill 相关信息"""
    name: str
    description: str
    content: str
    location: str
    skill_type: SkillType


def _parse_frontmatter(text: str, skill_dir: Path) -> tuple[Any, str]:
    """拆出 SKILL.md 的 YAML frontmatter 与正文。

    Args:
        text: SKILL.md 全文。
        skill_dir: 技能目录，用于日志。

    Returns:
        (frontmatter 解析结果, 正文)；没有 frontmatter 时返回 ({}, 原文)，
        YAML 解析失败时返回 ({}, 剥离 frontmatter 后的正文)；YAML 非映射时返回解析值
        （假值会被 `or {}` 归一为 {}）。
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        _LOGGER.exception(f"[Skill] Load skill {skill_dir} error")
        meta = {}

    return meta, parts[2].strip()


def _read_state() -> tuple[set[str], str | None]:
    """读 .skills.json：缺失 = 空状态；损坏 / 结构非法 = 全部启用 + 可展示错误。

    Returns:
        (禁用技能名集合, 错误信息或 None)。
    """
    state_file = PATHS.SKILL_STATE_FILE
    if not state_file.exists():
        return set(), None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOGGER.error(f"[Skill] Read state {state_file} error: {exc!r}")
        return set(), f"failed to read .skills.json: {exc}"
    if not isinstance(data, dict):
        _LOGGER.error(f"[Skill] State {state_file} is not an object")
        return set(), "invalid .skills.json: expected an object"
    disabled = data.get("disabled", [])
    if not isinstance(disabled, list) or not all(isinstance(name, str) for name in disabled):
        _LOGGER.error(f"[Skill] State {state_file} has invalid 'disabled' list")
        return set(), "invalid .skills.json: 'disabled' must be a list of names"
    return set(disabled), None


def _write_state(disabled: set[str]) -> None:
    """把禁用技能名集合写入 .skills.json。

    Args:
        disabled: 要写入的禁用技能名。
    """
    state_file = PATHS.SKILL_STATE_FILE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


def _load_state() -> None:
    """从磁盘重载技能启用状态与错误信息。"""
    global _DISABLED, _STATE_ERROR
    _DISABLED, _STATE_ERROR = _read_state()


def scan_skills() -> None:
    """扫描 global / user / project 三来源的 Skills；同名按 project > user > global 取用。

    扫描完成后加载 /skills 的启用状态，并剔除已经不在注册表里的禁用名（用户已删掉的技能
    不应永久占着状态）。状态文件损坏时保持全员启用，不做回写。
    """
    global _DISABLED
    SKILL_REGISTRY.clear()

    for skill_type, skills_dir in _skill_sources():
        if not skills_dir.exists():
            continue
        for skill_directory in sorted(skills_dir.iterdir()):
            if not skill_directory.is_dir():
                continue
            manifest = skill_directory / "SKILL.md"
            if not manifest.exists():
                continue
            raw = manifest.read_text(encoding="utf-8")
            meta, content = _parse_frontmatter(raw, skill_directory)
            name = meta.get("name", skill_directory.name)
            description = meta.get("description", "")
            SKILL_REGISTRY[name] = Skill(
                name=name,
                description=description,
                content=content,
                location=str(skill_directory.resolve()),
                skill_type=skill_type,
            )
            _LOGGER.info(f"[Skill] Load {skill_type} skill {skill_directory} success")

    _load_state()
    if _STATE_ERROR is not None:
        return

    stale = _DISABLED - SKILL_REGISTRY.keys()
    if not stale:
        return
    _DISABLED -= stale
    try:
        _write_state(_DISABLED)
    except OSError as exc:
        _LOGGER.error(f"[Skill] Write state {PATHS.SKILL_STATE_FILE} error: {exc!r}")


def is_skill_enabled(name: str) -> bool:
    """该技能是否启用；未在状态文件里的名字缺省启用。

    Args:
        name: 技能名。

    Returns:
        是否启用。
    """
    return name not in _DISABLED


def skill_state_error() -> str | None:
    """状态文件读取失败的原因；None 表示正常（含文件缺失）。

    Returns:
        可展示的错误信息或 None。
    """
    return _STATE_ERROR


def set_skill_enabled(name: str, enabled: bool) -> tuple[bool, str]:
    """写入一个技能的启用状态；落盘失败时回滚内存状态。

    Args:
        name: 技能名。
        enabled: 目标启用状态。

    Returns:
        (是否成功, 可直接展示的信息)。
    """
    global _STATE_ERROR
    if name not in SKILL_REGISTRY:
        return False, f"Skill not found: {name}"

    before = set(_DISABLED)
    if enabled:
        _DISABLED.discard(name)
        action = "Enabled"
    else:
        _DISABLED.add(name)
        action = "Disabled"
    try:
        _write_state(_DISABLED)
    except Exception as exc:  # 文件系统 / 编码错误都回滚，不能让 UI 动作抛穿
        _DISABLED.clear()
        _DISABLED.update(before)
        _LOGGER.error(f"[Skill] Write state {PATHS.SKILL_STATE_FILE} error: {exc!r}")
        return False, f"Failed to save skill state: {exc}"
    _STATE_ERROR = None  # 成功写出合法状态即视为故障已修复
    return True, f"{action} skill '{name}'"


def _enabled_skill_names() -> str:
    """错误信息里的可用技能列表：只列启用项。

    Returns:
        以逗号分隔的技能名；无启用项时为 "(none)"。
    """
    return ", ".join(name for name in SKILL_REGISTRY if name not in _DISABLED) or "(none)"


def load_skills() -> dict[str, Skill] | None:
    """模型可见的技能表：只含启用项；没有可用技能时返回 None。

    Returns:
        技能名到 Skill 的映射，或 None。
    """
    if not SKILL_REGISTRY:
        return None
    if not _DISABLED:
        return SKILL_REGISTRY
    enabled = {name: skill for name, skill in SKILL_REGISTRY.items() if name not in _DISABLED}
    return enabled or None


def load_skill(name: str) -> str:
    """加载一个技能的完整内容；未找到或已禁用时返回可展示提示。

    Args:
        name: 技能名。

    Returns:
        技能内容或错误提示文本。
    """
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Skill not found: {name}\n\nAvailable skills:\n{_enabled_skill_names()}"
    if name in _DISABLED:
        return (f"Skill '{name}' is disabled. Enable it in /skills.\n\n"
                f"Available skills:\n{_enabled_skill_names()}")
    return SKILL_MESSAGE_TEMPLATE.format(
        location=skill.location or "(none)",
        content=skill.content or "(none)",
    )


# todo: install skill
async def install_skill() -> None:
    """安装技能（尚未实现）。"""
    pass
