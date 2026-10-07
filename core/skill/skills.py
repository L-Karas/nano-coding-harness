"""
Skills Module
"""
import yaml

from core.config import SKILL_DIR

SKILL_REGISTRY: dict[str, dict] = {}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    return meta, parts[2].strip()


def scan_skills():
    SKILL_REGISTRY.clear()
    if not SKILL_DIR.exists():
        return

    for skill_directory in sorted(SKILL_DIR.iterdir()):
        if not skill_directory.is_dir():
            continue
        manifest = skill_directory / "SKILL.md"
        if not manifest.exists():
            continue
        raw = manifest.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(raw)
        name = meta.get("name", skill_directory.name)
        description = meta.get("description", "")
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "content": raw,
        }


def load_skills() -> dict | None:
    return SKILL_REGISTRY if SKILL_REGISTRY else None


def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available_skills = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}\n\nAvailable skills:\n{available_skills}"
    return skill.get("content")


# todo: install skill
async def install_skill() -> str:
    pass
