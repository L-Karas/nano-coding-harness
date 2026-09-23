"""技能列表弹窗（/skills）。"""

from __future__ import annotations

from core.tui.screens.base import _NamedListScreen


class SkillsScreen(_NamedListScreen):
    """技能列表弹窗（/skills）：列出全部已扫描技能，行 = ● 技能名（绿色加粗）+ 描述（暗灰多行）；
    Enter/点击选中 → dismiss 技能名，由 ChatApp 填入输入条并发送 "Invoke skill '<name>'"；
    Esc 关闭。skills 由调用方从 core.skill.SKILL_REGISTRY 取值。"""

    TITLE = "🧩 Available Skills"
    HINT = "  ↑/↓ browse    Enter invoke    Esc close"
    LIST_ID = "skills-list"

    def __init__(self, skills: list[dict]) -> None:
        super().__init__([(skill["name"], skill.get("description", "")) for skill in skills])
