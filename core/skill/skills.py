"""
Skills Module
"""
import yaml

from core.config import SKILL_DIR

SKILL_REGISTRY: dict[str, dict] = {}

if not SKILL_DIR.exists():
    SKILL_DIR.mkdir(parents=True, exist_ok=True)


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


def list_skills():
    if not SKILL_REGISTRY:
        return "(none)"
    return "\n".join(f"- {skill['name']}: {skill['description']}" for skill in SKILL_REGISTRY.values())


def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available_skills = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}\n\nAvailable skills:\n{available_skills}"
    return skill.get("content")


scan_skills()
