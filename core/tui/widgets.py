"""输入框与补全：_CommandInput（/ 指令与 @ 文件两组候选）+ 候选匹配纯函数。

弹窗部件已按功能拆到 core/tui/screens/；本模块仍转出会话弹窗与原地确认，
兼容既有导入路径（tests、外部调用）。
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text
from textual import events
from textual.widgets import Input, OptionList, TextArea
from textual.widgets.option_list import Option

from core.tui.screens import SessionPickerScreen, _InlineConfirm  # noqa: F401  兼容旧导入
from core.tui.utils import (
    SLASH_COMMAND_ALIASES,
    SLASH_COMMANDS,
    list_project_files,
    slash_command_rows,
)

# / 指令候选行的显示文本（指令名列定宽 + 简短说明，列宽与分段规则见 utils.slash_command_rows）
_CMD_ROWS: dict[str, tuple[str, str]] = dict(zip(SLASH_COMMANDS, slash_command_rows()))
_CMD_DESC_STYLE = "#94a3b8"


def _cmd_candidates(value: str) -> list[str]:
    """/ 指令候选：整行以 / 开头且未含空白时按前缀过滤；指令或别名完整输入后不再提示。"""
    if not (value.startswith("/") and " " not in value):
        return []
    candidates = []
    for cmd in SLASH_COMMANDS:
        names = (cmd, *SLASH_COMMAND_ALIASES.get(cmd, ()))
        if value not in names and any(name.startswith(value) for name in names):
            candidates.append(cmd)
    return candidates


def _cmd_row(cmd: str) -> Text:
    """候选行 = 指令名列（定宽，含别名括注）+ 空距 + 简短说明（淡灰）；
    各行说明左端对齐于同一列。"""
    label, desc = _CMD_ROWS[cmd]
    row = Text(label, no_wrap=True)
    row.append(f"    {desc}", style=_CMD_DESC_STYLE)
    return row


def _ancestor_dirs(files: list[str]) -> set[str]:
    """list_project_files 只返回文件；全部祖先目录（含中间层）由文件路径推导。"""
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        dirs.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    return dirs


def _level_entries(files: list[str], level: str, rem: str) -> set[str]:
    """level 目录的直接子项（同级列表）：名称含 rem 才入选；目录显示 name/。"""
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
    """@ 提及的补全候选：相对项目根的路径，目录以 / 结尾；大小写不敏感的子串匹配。

    - 空输入 → 项目根同级列表（目录 name/，不递归不展开）；
    - 纯名称（无 /，如 ui）→ 全树搜索名称含 query 的文件与目录，命中深层展示完整路径；
    - 带 / → 目录前缀必须真实存在，只在其直接子项中匹配（同级规则，逐级下钻）。"""
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
    else:  # 根目录同级列表
        hits = _level_entries(files, "", "")
    hits.discard(query)  # 已完整输入条目名则不再提示（对齐 / 指令的完整输入即隐藏）
    return sorted(hits)


# 换行键组：Shift+Enter / Ctrl+Enter 需终端支持 kitty 键盘协议才可分（如 WezTerm、Linux
# 原生终端）；Ctrl+J 发送 LF，与 Enter 的 CR 在所有终端都区分，是通用兜底。
_NEWLINE_KEYS = ("shift+enter", "ctrl+j", "ctrl+enter")


class _CommandInput(TextArea):
    """带指令与 @ 文件补全的多行输入框：内容超出终端宽度自动换行多行显示（TextArea）。

    - / 指令：整行以 / 开头且未含空白时，在输入框上方弹出候选 OptionList（仅此场景可见），
      按前缀过滤，↑/↓ 选择、Tab/Enter 接受、Esc 关闭；
    - @ 文件提及：行首/空白后起始的 @ 弹出项目文件候选，Tab/Enter/点击把 @路径 补进输入
      （不发送）；带目录前缀时同级逐级下钻、目录显示为 name/，匹配到文件夹内内容时展示完整路径；
    - 普通消息 Enter 提交、Shift+Enter / Ctrl+J 换行。

    两组候选同刻至多一组非空，互斥显示。按键全部在 _on_key 拦截：TextArea 的 _on_key
    会消费 Enter（插换行）/Tab（缩进）/Esc，普通 BINDINGS 要等键未被消费、冒泡到 App
    才检查，永远轮不到。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: list[str] = []  # / 指令补全候选
        self._file_candidates: list[str] = []  # @ 文件补全候选（完整相对路径；目录以 / 结尾）

    # ---------- 候选列表（/ 指令与 @ 文件提及；同刻至多一组候选，互斥显示） ----------

    def _list_widget(self, list_id: str) -> Optional[OptionList]:
        """按 id 取候选 OptionList；部件尚未挂载时返回 None（早期事件空转）。"""
        try:
            return self.screen.query_one(f"#{list_id}", OptionList)
        except Exception:
            return None

    def _sync_list(self, list_id: str, entries: list[tuple[str, Text]]) -> None:
        """重建候选列表（选项 id = 候选串）；无候选则隐藏，部件未挂载则空转。"""
        ol = self._list_widget(list_id)
        if ol is None:
            return
        ol.set_options(Option(prompt, id=value) for value, prompt in entries)
        ol.styles.display = "block" if entries else "none"
        ol.highlighted = 0 if entries else None  # None→0 触发 watch：高亮首项并滚回顶部

    def _find_mention(self) -> Optional[tuple[int, int, str]]:
        """光标所在行内、光标之前最后一个「行首/空白后」的 @；
        返回 (行, @列, @ 到光标之间的查询串)，无有效 @ 返回 None。"""
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
        """输入变化后重算两组候选并重建对应列表（选项为纯数据，整批重建全同步）。"""
        self._candidates = _cmd_candidates(self.text.strip())
        mention = self._find_mention()
        # ponytail: 每次按键全树扫描（utils.list_project_files 已剪 .git/venv 等）；
        # 项目极大导致输入卡顿时再按目录树增量缓存
        self._file_candidates = _match_project_entries(mention[2]) if mention else []
        self._sync_list("cmd-suggest", [(cmd, _cmd_row(cmd)) for cmd in self._candidates])
        self._sync_list("file-suggest", [(c, Text(c, no_wrap=True)) for c in self._file_candidates])

    def _apply_cmd_candidate(self) -> None:
        """接受高亮的 / 指令候选：整行替换为指令并复位光标（Enter 时随后提交）。"""
        ol = self._list_widget("cmd-suggest")
        if ol is not None and ol.highlighted is not None:
            self.text = self._candidates[ol.highlighted]
            self.cursor_location = (0, len(self.text))

    def apply_file_candidate(self, index: Optional[int] = None) -> bool:
        """把第 index 个（缺省 = 列表高亮项）文件候选补进输入：以 @完整路径 替换 @ 到光标处
        （不发送消息）。目录候选自带结尾 /，随后的刷新即展开其同级内容；返回是否成功应用。"""
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
        """↑/↓：在可见候选中循环移动高亮（两组候选互斥，同刻至多一组非空）。"""
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
        """拦截 Enter/换行/Tab 与候选导航键，其余交给 TextArea 编辑。
        - / 指令候选：Enter/Tab 接受（整行替换），Enter 随即提交消息；
        - @ 文件候选：Enter/Tab/点击接受，只把 @完整路径 补进输入不发送；
        - 无候选时：Enter 提交，Tab 吞掉（不插入缩进），方向键/Esc 交回 TextArea。"""
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
