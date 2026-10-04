"""输入框与补全：_CommandInput（/ 指令 + @ 文件两组候选）+ 候选匹配纯函数 + clarify 选项列表。

SessionPickerScreen / _InlineConfirm 仍从本模块转出，兼容历史导入路径（tests 等）。
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, OptionList, TextArea
from textual.widgets.option_list import Option

from core.tui.screens.base import _InlineConfirm  # noqa: F401  兼容旧导入
from core.tui.screens.session import SessionPickerScreen  # noqa: F401  兼容旧导入
from core.tui.utils import (
    SLASH_COMMAND_ALIASES,
    SLASH_COMMANDS,
    list_project_files,
    slash_command_rows,
)

# / 指令候选行：指令名列定宽（utils.slash_command_rows）+ 简短说明
_CMD_ROWS: dict[str, tuple[str, str]] = dict(zip(SLASH_COMMANDS, slash_command_rows()))
_CMD_DESC_STYLE = "#94a3b8"


def _cmd_candidates(value: str) -> list[str]:
    """/ 指令候选：整行以 / 开头且未含空白时按前缀过滤（含别名）；指令完整输入后不再提示。"""
    if not (value.startswith("/") and " " not in value):
        return []
    candidates = []
    for cmd in SLASH_COMMANDS:
        names = (cmd, *SLASH_COMMAND_ALIASES.get(cmd, ()))
        if value not in names and any(name.startswith(value) for name in names):
            candidates.append(cmd)
    return candidates


def _cmd_row(cmd: str) -> Text:
    """候选行 = 指令名列（定宽，含别名括注）+ 空距 + 简短说明（淡灰）。"""
    label, desc = _CMD_ROWS[cmd]
    row = Text(label, no_wrap=True)
    row.append(f"    {desc}", style=_CMD_DESC_STYLE)
    return row


def _ancestor_dirs(files: list[str]) -> set[str]:
    """全部祖先目录（含中间层），由文件路径推导（list_project_files 只返回文件）。"""
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        dirs.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    return dirs


def _level_entries(files: list[str], level: str, rem: str) -> set[str]:
    """level 目录的直接子项：名称含 rem 才入选；目录显示 name/。"""
    prefix = f"{level}/" if level else ""
    r = rem.lower()
    entries: dict[str, str] = {}  # 子项名 -> 展示串（目录带结尾 /；同名去重）
    for f in files:
        if f.startswith(prefix):
            name, _, deeper = f[len(prefix):].partition("/")
            if r in name.lower():
                entries[name] = f"{prefix}{name}/" if deeper else f
    return set(entries.values())


def _match_project_entries(query: str) -> list[str]:
    """@ 提及补全候选（相对项目根，目录以 / 结尾，大小写不敏感子串）：

    - 空输入 → 根同级列表；纯名称 → 全树匹配名称并展示完整路径；
    - 带 / → 目录前缀必须真实存在，只在其直接子项中逐级下钻。"""
    files = list_project_files()
    dirs = _ancestor_dirs(files)
    if "/" in query:
        level, rem = (query[:-1], "") if query.endswith("/") else query.rsplit("/", 1)
        if level and level not in dirs:
            return []  # 目录前缀不存在：还没输入到任何文件夹内容里
        hits = _level_entries(files, level, rem)
    elif query:  # 纯名称：全树相似匹配
        q = query.lower()
        hits = {d + "/" for d in dirs if q in d.rsplit("/", 1)[-1].lower()}
        hits |= {f for f in files if q in f.rsplit("/", 1)[-1].lower()}
    else:
        hits = _level_entries(files, "", "")
    hits.discard(query)  # 已完整输入条目名则不再提示
    return sorted(hits)


# 换行键：Shift/Ctrl+Enter 需终端支持 kitty 键盘协议；Ctrl+J 发 LF，各终端都与 Enter 可区分。
_NEWLINE_KEYS = ("shift+enter", "ctrl+j", "ctrl+enter")


class _CommandInput(TextArea):
    """带 / 指令与 @ 文件补全的多行输入框（TextArea）。

    - / 指令：整行以 / 开头且未含空白时在输入框上方弹候选列表，↑/↓ 选、Tab/Enter 接受、Esc 关；
    - @ 文件：行首/空白后的 @ 弹项目文件候选，Tab/Enter/点击补进输入（不发送）；
    - 普通消息 Enter 发送、Shift+Enter / Ctrl+J 换行；ctrl+←/→ 转发给 App 调右栏宽度。

    两组候选同刻至多一组可见。按键全部在 _on_key 拦截：TextArea 会消费 Enter/Tab/Esc，
    普通 BINDINGS 等不到冒泡。"""

    BINDINGS = [  # 覆盖 TextArea 的 ctrl+←/→ 词移光标（仅本部件，弹窗内不受影响）
        ("ctrl+left", "app.widen_info_panel", "Widen info panel"),
        ("ctrl+right", "app.narrow_info_panel", "Narrow info panel"),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: list[str] = []  # / 指令候选
        self._file_candidates: list[str] = []  # @ 文件候选（完整相对路径；目录以 / 结尾）

    # ---------- 候选列表 ----------

    def _list_widget(self, list_id: str) -> Optional[OptionList]:
        """按 id 取候选 OptionList；未挂载时返回 None（早期事件空转）。"""
        try:
            return self.screen.query_one(f"#{list_id}", OptionList)
        except Exception:
            return None

    def _sync_list(self, list_id: str, entries: list[tuple[str, Text]]) -> None:
        """整批重建候选列表（选项 id = 候选串）；空则隐藏，未挂载则空转。"""
        ol = self._list_widget(list_id)
        if ol is None:
            return
        ol.set_options(Option(prompt, id=value) for value, prompt in entries)
        ol.styles.display = "block" if entries else "none"
        ol.highlighted = 0 if entries else None  # None→0 触发高亮首项并滚回顶部

    def _find_mention(self) -> Optional[tuple[int, int, str]]:
        """光标前最后一个「行首/空白后」的 @ → (行, @列, @ 到光标之间的查询串)。"""
        line, col = self.cursor_location
        lines = self.text.split("\n")
        if line >= len(lines):
            return None
        before = lines[line][:col]
        at = before.rfind("@")
        if at < 0 or (at > 0 and not before[at - 1].isspace()):
            return None
        return line, at, before[at + 1:]

    def _refresh_suggestions(self) -> None:
        """输入变化后重算两组候选并重建对应列表。"""
        self._candidates = _cmd_candidates(self.text.strip())
        mention = self._find_mention()
        # ponytail: 每次按键全树扫描（已剪 .git/venv 等）；项目极大卡顿再按目录树增量缓存
        self._file_candidates = _match_project_entries(mention[2]) if mention else []
        self._sync_list("cmd-suggest", [(cmd, _cmd_row(cmd)) for cmd in self._candidates])
        self._sync_list("file-suggest", [(c, Text(c, no_wrap=True)) for c in self._file_candidates])

    def _apply_cmd_candidate(self) -> None:
        """接受高亮的 / 指令候选：整行替换并复位光标（Enter 时随后提交）。"""
        ol = self._list_widget("cmd-suggest")
        if ol is not None and ol.highlighted is not None:
            self.text = self._candidates[ol.highlighted]
            self.cursor_location = (0, len(self.text))

    def apply_file_candidate(self, index: Optional[int] = None) -> bool:
        """把第 index 个（缺省 = 高亮项）候选以 @完整路径 替换 @ 到光标处（不发送）。
        目录候选自带结尾 /，随后的刷新即展开其同级内容；返回是否成功应用。"""
        if index is None:
            ol = self._list_widget("file-suggest")
            index = ol.highlighted if ol is not None else None
        mention = self._find_mention()
        if mention is None or index is None or not 0 <= index < len(self._file_candidates):
            return False
        line, at, _ = mention
        _, col = self.cursor_location
        lines = self.text.split("\n")
        candidate = self._file_candidates[index]
        lines[line] = lines[line][:at] + "@" + candidate + lines[line][col:]
        self.text = "\n".join(lines)
        self.cursor_location = (line, at + 1 + len(candidate))
        self._refresh_suggestions()
        return True

    def _cycle_highlight(self, delta: int) -> None:
        """↑/↓：在可见候选中循环移动高亮（两组候选互斥）。"""
        for list_id, candidates in (("file-suggest", self._file_candidates),
                                    ("cmd-suggest", self._candidates)):
            ol = self._list_widget(list_id)
            if candidates and ol is not None:
                ol.highlighted = ((ol.highlighted or 0) + delta) % len(candidates)
                return

    def _close_suggestions(self) -> None:
        """Esc：清空候选状态并隐藏两个列表（之后 Enter 恢复为普通发送）。"""
        self._candidates = []
        self._file_candidates = []
        self._sync_list("cmd-suggest", [])
        self._sync_list("file-suggest", [])

    # ---------- 键盘 ----------

    async def _on_key(self, event: events.Key) -> None:
        """候选导航 / Enter / Tab / 换行拦截；其余交 TextArea。无候选时 Enter 发送、Tab 吞掉。"""
        key = event.key
        has_candidates = bool(self._candidates or self._file_candidates)

        def consume() -> None:
            event.stop()
            event.prevent_default()

        if key == "up" and has_candidates:
            consume()
            self._cycle_highlight(-1)
        elif key == "down" and has_candidates:
            consume()
            self._cycle_highlight(1)
        elif key == "escape" and has_candidates:
            consume()
            self._close_suggestions()
        elif key in _NEWLINE_KEYS:
            consume()
            self.insert("\n")
        elif key == "enter":
            consume()
            if self._file_candidates:  # @ 文件候选：补进输入，不发送
                self.apply_file_candidate()
            else:
                if self._candidates:  # / 指令候选：整行替换后随 Enter 提交
                    self._apply_cmd_candidate()
                self.post_message(Input.Submitted(self, self.text))
        elif key == "tab":
            consume()
            if self._file_candidates:
                self.apply_file_candidate()
            elif self._candidates:
                self._apply_cmd_candidate()
        else:
            await super()._on_key(event)

    # ---------- 事件 ----------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        self._refresh_suggestions()


class ClarifyConfirmed(Message):
    """clarify 列表 Enter 确认（多选 = 提交已勾选；单选 = 选中高亮项）。"""


class _ClarifyList(OptionList):
    """clarify 选项列表：Enter 发 ClarifyConfirmed；Space 勾选由 App 的 space 绑定处理。"""

    BINDINGS = [Binding("enter", "confirm", "Confirm", show=False)]

    def action_confirm(self) -> None:
        self.post_message(ClarifyConfirmed())
