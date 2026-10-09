"""
Skills Module
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from core.config import SKILL_DIR, GLOBAL_SKILLS_DIR, PROJECT_SKILL_DIR
from core.log import get_logger

_LOGGER = get_logger(__name__)

SkillType = Literal["global", "user", "project"]

# 扫描来源：顺序即优先级（靠后覆盖靠前），project > user > global
SKILL_SOURCES: tuple[tuple[SkillType, Path], ...] = (
    ("global", GLOBAL_SKILLS_DIR),
    ("user", SKILL_DIR),
    ("project", PROJECT_SKILL_DIR),
)

SKILL_REGISTRY: dict[str, "Skill"] = {}


@dataclass
class Skill:
    """Skill 类，用于存储 skill 相关信息"""
    name: str
    description: str
    content: str
    skill_type: SkillType


def _parse_frontmatter(text: str, skill_dir: Path) -> tuple[dict, str]:
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


def scan_skills() -> None:
    """扫描 global / user / project 三来源的 Skills；同名按 project > user > global 取用。"""
    SKILL_REGISTRY.clear()

    for skill_type, skills_dir in SKILL_SOURCES:
        if not skills_dir.exists():
            continue
        for skill_directory in sorted(skills_dir.iterdir()):
            if not skill_directory.is_dir():
                continue
            manifest = skill_directory / "SKILL.md"
            if not manifest.exists():
                continue
            raw = manifest.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(raw, skill_directory)
            name = meta.get("name", skill_directory.name)
            description = meta.get("description", "")
            SKILL_REGISTRY[name] = Skill(name=name,
                                         description=description,
                                         content=raw,
                                         skill_type=skill_type)
            _LOGGER.info(f"[Skill] Load {skill_type} skill {skill_directory} success")


def load_skills() -> dict[str, Skill] | None:
    return SKILL_REGISTRY if SKILL_REGISTRY else None


def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available_skills = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}\n\nAvailable skills:\n{available_skills}"
    return skill.content or "(Skill no content)"


# todo: install skill
async def install_skill() -> str:
    pass
