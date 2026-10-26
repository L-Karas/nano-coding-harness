"""技能列表弹窗（/skills）。"""

from __future__ import annotations

from textual import events
from textual.widgets import OptionList

from core.skill.skills import Skill
from core.tui.screens.base import _NamedItem, _NamedListScreen


class SkillsScreen(_NamedListScreen):
    """技能列表弹窗（/skills）：行 = ● 技能名 [来源] + 描述。

    Enter/点击回传 (name, False)，由 ChatApp 立即发送 "[Invoke skill '<name>']"；Tab 回传
    (name, True)，由 ChatApp 把调用信息填入输入栏并聚焦，用户可继续写补充消息后一起发送；
    Esc 关闭。skills 来自 core.skill.SKILL_REGISTRY。"""

    TITLE = "Available Skills"
    HINT = "  ↑/↓ browse    Enter/click invoke    Tab invoke with message    Esc close"
    LIST_ID = "skills-list"
    SEARCH_PLACEHOLDER = "Search skills…"

    def __init__(self, skills: list[Skill]) -> None:
        super().__init__([_NamedItem(skill.name, skill.description, skill.skill_type) for skill in skills])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击：立即调用（with_message=False）。

        prevent_default 才会中断 Textual 沿 MRO 派发到 _NamedListScreen 的同名处理器
        （否则它会再 dismiss 一次字符串，导致重复 pop）。"""
        event.prevent_default()
        event.stop()
        if event.option_id is not None:
            self.dismiss((event.option_id, False))

    def on_key(self, event: events.Key) -> None:
        """Tab：回传当前高亮技能并附带补充消息（搜索栏聚焦时同样生效）。

        其余键直接返回：Textual 会继续沿 MRO 派发到 _ListPickerScreen.on_key，
        这里再调 super() 会让 ↑/↓ 被处理两次。"""
        if event.key != "tab":
            return
        event.prevent_default()
        event.stop()
        olist = self._list()
        if olist.highlighted is None:
            return
        option_id = olist.get_option_at_index(olist.highlighted).id
        if option_id is not None:
            self.dismiss((option_id, True))
