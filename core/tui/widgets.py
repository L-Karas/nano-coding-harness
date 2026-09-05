"""可复用部件与弹窗：会话列表行 / 会话选择弹窗 / 带指令与 @ 文件补全的输入框。

- _SessionRow + SessionPickerScreen：/sessions 弹窗（Enter 切换 / Delete 删除）；
- _match_project_entries 及辅助函数：@ 提及候选匹配（utils.list_project_files 之上的纯函数层）；
- _CommandInput：TextArea 子类，拦截按键实现 / 指令、@ 文件两组候选列表交互。
"""

from __future__ import annotations

from typing import Any, Optional

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text
from textual import events
from textual.containers import CenterMiddle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from core.tui.utils import SLASH_COMMANDS, list_project_files


class _SessionRow:
    """会话列表项：标题靠左、(current session) 标记随后、时间戳顶到行最右。

    OptionList 把每行 prompt 按行宽渲染后再尾部补白（默认左对齐），无法表达
    右对齐列；因此在渲染时按 OptionList 给到的实际行宽自排版：时间戳恒在行最右。
    宽度随终端布局变化，每次渲染按当时行宽重排，无需预知终端宽度；空间不足时
    先舍 (current) 标记、再截断标题（…），时间戳保持完整。"""

    _TITLE_STYLE = "#f8fafc"
    _MARK_STYLE = "#34d399"
    _TS_STYLE = "#94a3b8"

    def __init__(self, title: str, current: bool, timestamp: str) -> None:
        self._title = title
        self._current = current
        self._timestamp = timestamp

    def __rich_measure__(self, options: ConsoleOptions) -> Measurement:
        # 与普通文本一致：自然宽度 = 完整未截断行（超宽行由列表裁剪）
        full = f"{self._title}{'  (current session)' if self._current else ''}  {self._timestamp}"
        return Measurement(1, cell_len(full))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = max(0, options.max_width or 0)
        title = Text(self._title, style=self._TITLE_STYLE)
        ts = Text(f"  {self._timestamp}", style=self._TS_STYLE)
        row = Text()
        if width >= ts.cell_len:  # 常规宽度：标题 + 可选标记 + 空白填隙 + 时间戳
            free = width - ts.cell_len
            if title.cell_len > free:
                if free >= 1:
                    title.truncate(free, overflow="ellipsis")
                else:
                    title = Text("")
            row.append_text(title)
            free -= title.cell_len
            if self._current:
                mark = Text("  (current session)", style=self._MARK_STYLE)
                if mark.cell_len <= free:
                    row.append_text(mark)
                    free -= mark.cell_len
            if free > 0:
                row.append_text(Text(" " * free))
        row.append_text(ts)
        row.no_wrap = True  # 极端窄行不折行，超宽部分交给 OptionList 裁剪
        yield row


class SessionPickerScreen(ModalScreen[tuple[Optional[str], bool]]):
    """会话选择弹窗：Enter 切换 / Delete 删除 / Esc、q 取消。
    dismiss 结果: (选中的 session id 或 None, 列表是否已删空)。"""

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("q", "cancel", "取消"),
        ("delete", "remove_selected", "删除"),
    ]

    def __init__(self, manager: Any) -> None:
        super().__init__()
        self._manager = manager

    def compose(self) -> ComposeResult:
        yield CenterMiddle(
            Vertical(
                Static("📂 选择会话（current session 为当前会话）", classes="picker-title"),
                OptionList(id="sess-list"),
                Static("  ↑/↓ 选择    Enter 切换    Delete 删除    Esc/q 取消", classes="picker-hint"),
                classes="picker",
            )
        )

    def on_mount(self) -> None:
        self._reload()

    def _row(self, session) -> _SessionRow:
        return _SessionRow(
            session.title if session.title else session.id,
            current=session.id == self._manager.current_session,
            timestamp=str(session.timestamp)[:16],  # 只展示到分钟；排序仍按完整时间戳
        )

    def _reload(self) -> None:
        sessions = sorted(self._manager.load_session_list(), key=lambda s: s.timestamp, reverse=True)
        olist = self.query_one("#sess-list", OptionList)
        old_index = olist.highlighted
        olist.clear_options()
        olist.add_options([Option(self._row(s), id=s.id) for s in sessions])
        if sessions:
            olist.highlighted = min(old_index or 0, len(sessions) - 1)

    def action_cancel(self) -> None:
        self.dismiss((None, False))

    def action_remove_selected(self) -> None:
        olist = self.query_one("#sess-list", OptionList)
        if olist.highlighted is None:
            return
        option = olist.get_option_at_index(olist.highlighted)
        self._manager.delete_session(option.id)
        if not self._manager.load_session_list():
            self.dismiss((None, True))  # 删空后退出，让主界面显示空列表卡片（对齐旧逻辑）
        else:
            self._reload()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id:
            self.dismiss((event.option_id, False))


def _ancestor_dirs(files: list[str]) -> set[str]:
    """list_project_files 只返回文件；全部祖先目录（含中间层）由文件路径推导。"""
    dirs: set[str] = set()
    for f in files:
        pos = f.find("/")
        while pos != -1:
            dirs.add(f[:pos])
            pos = f.find("/", pos + 1)
    return dirs


def _level_entries(files: list[str], level: str, rem: str, query: str) -> list[str]:
    """level 目录的直接子项（同级列表）：名称含 rem 才入选；目录显示 name/；
    已完整输入过的条目不再提示。"""
    prefix = f"{level}/" if level else ""
    children: dict[str, str] = {}  # 子项名 -> 展示串（文件为完整路径，目录带结尾 /）
    for f in files:
        if not f.startswith(prefix):
            continue
        name, _, deeper = f[len(prefix):].partition("/")
        children[name] = f"{prefix}{name}/" if deeper else f
    r = rem.lower()
    return sorted(d for name, d in children.items() if r in name.lower() and d != query)


def _match_project_entries(query: str) -> list[str]:
    """@ 提及的补全候选：相对项目根的路径，目录以 / 结尾；大小写不敏感的子串相似匹配。

    - 空输入 → 项目根同级列表（目录 name/，不递归不展开）；
    - 纯名称（无 /，如 ui）→ 全树搜索名称含 query 的文件与目录，命中深层展示完整路径；
    - 带 / → 目录前缀必须真实存在，只在其直接子项中匹配（同级规则，逐级下钻）。"""
    files = list_project_files()
    dirs = _ancestor_dirs(files)
    if "/" in query:
        level, rem = (query[:-1], "") if query.endswith("/") else query.rsplit("/", 1)
        if level and level not in dirs:
            return []  # 目录前缀不存在：还没输入到任何文件夹内容里
        return _level_entries(files, level, rem, query)
    if not query:  # 根目录同级列表
        return _level_entries(files, "", "", "")
    q = query.lower()  # 纯名称：全树相似匹配
    hits = {d + "/" for d in dirs if q in d.rsplit("/", 1)[-1].lower()}
    hits |= {f for f in files if q in f.rsplit("/", 1)[-1].lower()}
    hits.discard(query)  # 已完整输入条目名则不再提示（对齐 / 指令的 c != value）
    return sorted(hits)


class _CommandInput(TextArea):
    """带指令补全的多行输入框：内容超出终端宽度自动换行多行显示（TextArea），
    输入 / 开头的指令前缀时，在输入框上方弹出候选 ListView
    （仅此场景可见），↑/↓ 选择、Tab/Enter 接受、Esc 关闭；普通消息 Enter 提交、
    Shift+Enter / Ctrl+J 换行（Shift+Enter 需终端支持 kitty 键盘协议，Ctrl+J 全终端可用）。

    按键全部在 _on_key 拦截：TextArea 的 _on_key 会消费 Enter（插换行）/Tab（缩进）/Esc，
    普通 BINDINGS 要等键未被消费、冒泡到 App 才检查，永远轮不到；拦截后再直接处理。
    另外支持 @ 文件提及：行内输入行首/空白后起始的 @ 时弹出 ListView 展示项目文件，
    输入按名称相似匹配（ui → core/tui/、ui_textual.py）；带目录前缀时同级逐级下钻、
    目录显示为 name/，匹配到文件夹内内容时展示完整路径；Tab/Enter/点击把 @路径 补进输入。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: list[str] = []  # / 指令补全候选
        self._hl = 0  # 高亮项在 _candidates 中的下标
        self._file_candidates: list[str] = []  # @ 文件补全候选（完整相对路径；目录以 / 结尾）
        self._file_hl = 0  # 高亮项在 _file_candidates 中的下标

    # ---------- 候选列表（/ 指令与 @ 文件提及；同刻至多一组候选，互斥显示） ----------

    def _list_widget(self, list_id: str) -> Optional[ListView]:
        """按 id 取候选 ListView；部件尚未挂载时返回 None（早期事件空转）。"""
        try:
            return self.screen.query_one(f"#{list_id}", ListView)
        except Exception:
            return None

    def _hide_list(self, lst: Optional[ListView]) -> None:
        """隐藏候选列表并清除高亮（None：部件尚未挂载，空转）。"""
        if lst is not None:
            lst.styles.display = "none"
            lst.index = None

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
        return (line, at, before[at + 1:])

    def _refresh_suggestions(self) -> None:
        """输入变化后重算两组候选并刷新对应列表。"""
        lv = self._list_widget("cmd-suggest")
        if lv is not None:
            self._refresh_cmd_suggestions(lv)
        flv = self._list_widget("file-suggest")
        if flv is not None:
            self._refresh_file_suggestions(flv)

    def _refresh_cmd_suggestions(self, lv: ListView) -> None:
        """/ 指令：整行以 / 开头且未含空白时按前缀过滤候选（完整指令不再提示）。"""
        value = self.text.strip()
        self._candidates = [c for c in SLASH_COMMANDS
                            if value.startswith("/") and " " not in value
                            and c.startswith(value) and c != value]
        self._hl = 0
        for item, cmd in zip(lv.children, SLASH_COMMANDS):  # 4 个子项固定挂载，仅切换 display
            item.styles.display = "block" if cmd in self._candidates else "none"
        if self._candidates:
            lv.styles.display = "block"
            lv.index = SLASH_COMMANDS.index(self._candidates[0])
        else:
            self._hide_list(lv)

    def _refresh_file_suggestions(self, flv: ListView) -> None:
        """@ 文件提及：候选随输入重建（匹配规则见 _match_project_entries）；无提及/未命中则隐藏。"""
        mention = self._find_mention()
        if mention is not None:
            # ponytail: 每次按键全树扫描（utils.list_project_files 已剪 .git/venv 等）；
            # 项目极大导致输入卡顿时再按目录树增量缓存
            self._file_candidates = _match_project_entries(mention[2])
        else:
            self._file_candidates = []
        self._file_hl = 0
        if not self._file_candidates:
            self._hide_list(flv)
            return
        flv.remove_children()
        flv.mount(*(ListItem(Label(Text(c, no_wrap=True))) for c in self._file_candidates))
        flv.styles.display = "block"
        self._sync_highlight()

    def _apply_cmd_candidate(self) -> None:
        """接受高亮的 / 指令候选：整行替换为指令并复位光标（Enter 时随后提交）。"""
        self.text = self._candidates[self._hl]
        self.cursor_location = (0, len(self.text))

    def _apply_file_candidate(self, hl: int) -> bool:
        """把第 hl 个文件候选补进输入：以 @完整路径 替换 @ 到光标处（不发送消息）。
        目录候选自带结尾 /，随后的刷新即展开其同级内容；返回是否成功应用。"""
        if not (0 <= hl < len(self._file_candidates)):
            return False
        mention = self._find_mention()
        if mention is None:
            return False
        line, at, _ = mention
        _, col = self.cursor_location
        lines = self.text.split("\n")
        whole = lines[line]
        cand = self._file_candidates[hl]
        lines[line] = whole[:at] + "@" + cand + whole[col:]
        self.text = "\n".join(lines)
        self.cursor_location = (line, at + 1 + len(cand))
        self._refresh_suggestions()
        return True

    def _sync_highlight(self) -> None:
        """列表高亮跟随当前候选下标（cmd 子项固定，需由候选映射回其全局下标）。"""
        if self._candidates:
            lv = self._list_widget("cmd-suggest")
            if lv is not None:
                lv.index = SLASH_COMMANDS.index(self._candidates[self._hl])
        elif self._file_candidates:
            flv = self._list_widget("file-suggest")
            if flv is not None:
                flv.index = self._file_hl
                try:
                    flv.scroll_to_widget(flv.children[self._file_hl])  # 长列表保持高亮项可见
                except Exception:
                    pass

    def _cycle_highlight(self, delta: int) -> None:
        """↑/↓：在可见候选中循环移动高亮（两组候选互斥，同刻至多一组非空）。"""
        if self._file_candidates:
            self._file_hl = (self._file_hl + delta) % len(self._file_candidates)
        else:
            self._hl = (self._hl + delta) % len(self._candidates)
        self._sync_highlight()

    def _close_suggestions(self) -> None:
        """Esc：清空候选状态并隐藏两个列表（之后 Enter 恢复为普通发送）。"""
        self._candidates = []
        self._file_candidates = []
        self._hide_list(self._list_widget("cmd-suggest"))
        self._hide_list(self._list_widget("file-suggest"))

    # ---------- 键盘 ----------

    async def _on_key(self, event: events.Key) -> None:
        """拦截 Enter/换行/Tab 与候选导航键，其余交给 TextArea 编辑。
        - / 指令候选：Enter/Tab 接受（整行替换），Enter 随即提交消息；
        - @ 文件候选：Enter/Tab/点击接受，只把 @完整路径 补进输入不发送
          （@ 提及后通常还有正文，列表关闭后再按一次 Enter 才是发送）。
        换行键组：shift+enter / ctrl+enter 需终端支持 kitty 键盘协议才可分（如 WezTerm、
        Linux 原生终端）；ctrl+j 发送 LF，与 Enter 的 CR 在所有终端都区分，是通用兜底。"""
        key = event.key
        newline_keys = ("shift+enter", "ctrl+j", "ctrl+enter")
        has_candidates = bool(self._candidates or self._file_candidates)
        if key not in ("enter", "tab", *newline_keys) and not (
                key in ("up", "down", "escape") and has_candidates):
            await super()._on_key(event)  # 普通编辑键 / 无候选时的方向键：交回 TextArea
            return
        event.stop()
        event.prevent_default()
        if key in newline_keys:
            self.insert("\n")
        elif key == "escape":
            self._close_suggestions()
        elif key in ("up", "down"):
            self._cycle_highlight(-1 if key == "up" else 1)
        elif self._file_candidates:  # enter / tab：优先 @ 文件候选 → 补进输入，不发送
            self._apply_file_candidate(self._file_hl)
        elif self._candidates:  # / 指令候选 → 整行替换，Enter 随消息一并提交
            self._apply_cmd_candidate()
            if key == "enter":
                self.post_message(Input.Submitted(self, self.text))
        elif key == "enter":  # 无候选：普通提交（Tab 无候选时按原行为吞掉）
            self.post_message(Input.Submitted(self, self.text))

    # ---------- 事件 ----------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        self._refresh_suggestions()
