"""技能列表弹窗（/skills）。"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from core.skill.skills import (
    Skill,
    is_skill_enabled,
    set_skill_enabled,
    skill_state_error,
)
from core.tui.screens.base import _entry_row, _NamedItem, _NamedListScreen, _rebuild_options

_STATE_ID = "__skill_state__"


class SkillsScreen(_NamedListScreen):
    """技能列表弹窗（/skills）：行 = 状态字形 技能名 [来源] + 描述。

    Enter/点击回传 (name, False)，由 ChatApp 立即发送 "[Invoke skill '<name>']"；Tab 回传
    (name, True)，由 ChatApp 把调用信息填入输入栏并聚焦，用户可继续写补充消息后一起发送；
    Ctrl+E 切换选中技能的启用状态（落盘到 .harness/.skills.json）；Esc 关闭。
    禁用技能显示为红色 ○，Enter/Tab 只提示、不调用；状态文件损坏时列表顶部显示一条
    合成故障行，但所有技能仍按启用处理（fail-open）。"""

    TITLE = "Available Skills"
    HINT = ("  ↑/↓ browse    Enter/click invoke    Tab invoke with message"
            "    Ctrl+E enable/disable    Esc close")
    LIST_ID = "skills-list"
    SEARCH_PLACEHOLDER = "Search skills…"
    BINDINGS = [Binding("ctrl+e", "toggle_selected", "Enable/Disable", priority=True)]

    def __init__(self, skills: list[Skill]) -> None:
        super().__init__([_NamedItem(skill.name, skill.description, skill.skill_type)
                          for skill in skills])

    def _reload(self) -> None:
        rows = []
        error = skill_state_error()
        if error and not self._query:  # 搜索时隐藏故障行，避免干扰过滤结果
            rows.append(Option(_entry_row(".skills.json", error, enabled=False), id=_STATE_ID))
        for item in self._items:
            if not self._match(item.name):
                continue
            rows.append(Option(
                _entry_row(item.name, item.description, item.tag,
                           enabled=is_skill_enabled(item.name)),
                id=item.name,
            ))
        _rebuild_options(self._list(), rows)
        olist = self._list()
        if olist.highlighted is not None:
            if olist.get_option_at_index(olist.highlighted).id == _STATE_ID:
                olist.highlighted = 1 if len(rows) > 1 else None

    def _highlighted_id(self) -> str | None:
        olist = self._list()
        if olist.highlighted is None:
            return None
        option_id = olist.get_option_at_index(olist.highlighted).id
        return None if option_id is None else str(option_id)

    def _reject_disabled(self, name: str) -> bool:
        """禁用项 / 状态故障行不可调用；返回是否应拦截本次操作。"""
        if name == _STATE_ID:
            self.app.notify("Skill state file failed to load; all skills are treated as enabled.",
                            title="⚠️ Skills", severity="error")
            return True
        if is_skill_enabled(name):
            return False
        self.app.notify(f"Skill '{name}' is disabled. Press Ctrl+E to enable it.",
                        title="⛔ Skills")
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击：立即调用（with_message=False）。

        prevent_default 才会中断 Textual 沿 MRO 派发到 _NamedListScreen 的同名处理器
        （否则它会再 dismiss 一次字符串，导致重复 pop）。"""
        event.prevent_default()
        event.stop()
        if event.option_id is None:
            return
        name = str(event.option_id)
        if self._reject_disabled(name):
            return
        self.dismiss((name, False))

    def on_key(self, event: events.Key) -> None:
        """Tab：回传当前高亮技能并附带补充消息（搜索栏聚焦时同样生效）。

        其余键直接返回：Textual 会继续沿 MRO 派发到 _ListPickerScreen.on_key，
        这里再调 super() 会让 ↑/↓ 被处理两次。"""
        if event.key != "tab":
            return
        event.prevent_default()
        event.stop()
        name = self._highlighted_id()
        if name is None or self._reject_disabled(name):
            return
        self.dismiss((name, True))

    def action_toggle_selected(self) -> None:
        """Ctrl+E：切换当前高亮技能的启用状态并就地刷新。"""
        name = self._highlighted_id()
        if name is None or name == _STATE_ID:
            return
        ok, message = set_skill_enabled(name, not is_skill_enabled(name))
        if not ok:
            self.app.notify(message, title="⚠️ Skills", severity="error")
            return
        self.app.notify(message, title="✅ Skills")
        self._reload()
