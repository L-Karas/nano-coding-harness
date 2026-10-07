"""技能列表弹窗（/skills）。"""

from __future__ import annotations

from core.tui.screens.base import _NamedListScreen


class SkillsScreen(_NamedListScreen):
    """技能列表弹窗（/skills）：行 = ● 技能名 + 描述；Enter/点击 dismiss 技能名，由 ChatApp
    发送 "Invoke skill '<name>'"；Esc 关闭。skills 来自 core.skill.SKILL_REGISTRY。"""

    TITLE = "Available Skills"
    HINT = "  ↑/↓ browse    Enter invoke    Esc close"
    LIST_ID = "skills-list"
    SEARCH_PLACEHOLDER = "Search skills…"

    def __init__(self, skills: list[dict]) -> None:
        super().__init__([(skill["name"], skill.get("description", "")) for skill in skills])
