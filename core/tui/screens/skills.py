"""技能列表弹窗（/skills）。"""

from __future__ import annotations

from core.skill.skills import Skill
from core.tui.screens.base import _NamedItem, _NamedListScreen


class SkillsScreen(_NamedListScreen):
    """技能列表弹窗（/skills）：行 = ● 技能名 [来源] + 描述；Enter/点击 dismiss 技能名，由 ChatApp
    发送 "Invoke skill '<name>'"；Esc 关闭。skills 来自 core.skill.SKILL_REGISTRY。"""

    TITLE = "Available Skills"
    HINT = "  ↑/↓ browse    Enter invoke    Esc close"
    LIST_ID = "skills-list"
    SEARCH_PLACEHOLDER = "Search skills…"

    def __init__(self, skills: list[Skill]) -> None:
        super().__init__([_NamedItem(skill.name, skill.description, skill.skill_type) for skill in skills])
