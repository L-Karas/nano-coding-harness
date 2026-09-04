"""
Textual 版终端界面（core/tui/ui.py 的 Textual 重构，独立新模块，未改动原代码）

与原 Rich+prompt_toolkit 版不同：本模块是一个完整的 Textual 全屏 App，
聊天历史、流式 Markdown、工具卡片、会话切换、权限确认都在同一个事件循环里，
不再依赖 Live 原地重绘 / prompt_toolkit 行输入。

运行演示（无需 LLM/网络）:
    python -m core.tui.ui_textual

接入真实 Agent（需在 main.py 侧做等价替换，本模块不代劳）:
    from core.tui.ui_textual import run
    run(handle_query=agent_turn, session_manager=SESSION_MANAGER)
    # agent_turn(query) 在后台线程执行；内部渲染调用本模块的同名函数（线程安全）。

线程模型:
    - App 事件循环跑主线程；每轮用户输入在独立后台线程里调用 handle_query。
    - 渲染函数可从任意线程调用（App 线程内直接执行，其它线程经 call_from_thread 桥接）。
    - ask_permission 只能在非 App 线程调用（会阻塞等待用户在输入条中作答）。

模块级渲染函数（任意线程可调）：
render_user_input / render_tool_call / render_tool_result /
render_tool_result_diff / render_background_notification / render_sessions /
render_session_history / render_scope / stream_assistant_response / render_assistant_response /
render_thinking_status / render_tool_calling_status / ask_permission
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape
from rich.measure import Measurement
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import CenterMiddle, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, OptionList, Static, TextArea
from textual.widgets.option_list import Option

DEFAULT_TITLE = "🤖 Nano Coding Harness Agent"
DEFAULT_SUBTITLE = "输入问题后回车发送 · /new 新会话 · /sessions 切换 · /clear 清屏 · /exit 退出"
_PLACEHOLDER = "输入消息，Enter 发送 · Ctrl+J 换行（/exit 退出）"

# Tab 补全的指令表（/quit 是 /exit 的别名，不列入以免循环重复）
_SLASH_COMMANDS = ["/exit", "/clear", "/new", "/sessions"]

# 每类卡片主题：左边实线颜色 / 背景色（对齐原 ui.py 配色；标题行已去除，靠色条/底色区分消息类型）
_CARD_THEME = {
    "user":      {"bar": "#6b7280", "bg": "#374151"},
    "tool":      {"bar": "#f59e0b", "bg": "#261f0d"},
    "result":    {"bar": "#10b981", "bg": "#11221b"},
    "assistant": {"bar": "#c084fc", "bg": "#1e1b2e"},
    "notice":    {"bar": "#22d3ee", "bg": "#0f172a"},
    "sessions":  {"bar": "#38bdf8", "bg": "#0c1a2e"},
    "perm":      {"bar": "#f59e0b", "bg": "#3b2a10"},
}

# 加载动画帧（标准 Braille spinner，10 帧）；状态行 spin=True 时在文本前轮播
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _markup(text: str, justify: str = "left") -> Text:
    """把 Rich 标记字符串安全地转成 Text（用户内容里出现残缺标记时降级为纯文本）"""
    try:
        return Text.from_markup(text, justify=justify)
    except Exception:
        return Text(text, justify=justify)


def _theme_css() -> str:
    return "\n".join(
        f".card.{kind} {{ background: {t['bg']}; border-left: heavy {t['bar']}; }}"
        for kind, t in _CARD_THEME.items()
    )


_APP_CSS = f"""
App {{ background: ansi_default; }}  /* DOM 根背景用终端默认色（SGR 49），透明区域透出 terminal 原生背景；否则 Textual 会垫 #121212 主题色 */

Screen {{ background: transparent; }}  /* 背景透出终端原生底色，与 terminal 一致 */

#titlebar {{ width: 1fr; height: auto; background: #0c1a2e; padding: 0 2; }}
#titlebar > * {{ width: 1fr; }}
#chat {{ width: 1fr; height: 1fr; background: transparent; padding: 1 0; scrollbar-size-vertical: 0; }}  /* scrollbar-size-vertical:0 让内容（卡片画板）占满终端全宽；滚动仍由滚轮/scroll_end 驱动 */
#dock {{ dock: bottom; width: 1fr; height: auto; background: transparent; }}
#status {{ width: 1fr; height: 1; background: transparent; color: #a5b4fc; padding: 0 2; }}
#cmd-suggest {{ display: none; width: 1fr; height: auto; max-height: 6; margin: 0 2; background: #1e293b; border: tall #475569; }}
#cmd-suggest ListItem {{ height: 1; padding: 0 1; }}
#inputbar {{ width: 1fr; height: auto; background: transparent; border-top: heavy #475569; border-bottom: heavy #475569; }}
#prompt-mark {{ width: auto; color: #7dd3fc; padding: 0 0 0 2; }}
#prompt {{ width: 1fr; height: auto; max-height: 10; background: transparent; border: none; color: #f8fafc; padding: 0 1 0 1; }}  /* height:auto：空值 1 行；内容超出终端宽度自动换行、多行伸缩；超 10 行时内部滚动 */

.card {{ width: 1fr; height: auto; margin: 0 0 1 0; padding: 1 2; }}  /* 关键：height:auto 才能随内容量伸缩（Vertical 默认 height:1fr 会按视口均分并裁掉超出内容）；padding 1 2 让内容与边界留白；无左右 margin 保证卡片与终端同宽 */
.card > * {{ width: 1fr; }}
.card .card-body {{ text-align: left; }}
{_theme_css()}

.picker {{
    width: 76%; height: 80%;
    padding: 1 2;
}}
.picker {{ background: transparent; border: round #38bdf8; }}  /* 背景透出终端原生底色（与聊天区一致）；会话列表本身透明，行高亮仍可见 */

ModalScreen {{ background: transparent; }}  /* 去掉默认的主题色半透明遮罩，整屏保持 terminal 底色 */
.picker-title {{ width: 1fr; text-align: center; color: #38bdf8; padding: 0 0 1 0; }}
.picker-hint {{ width: 1fr; color: #64748b; padding: 1 0 0 0; }}
#sess-list {{ border: none; background: transparent; height: 1fr; margin-top: 1; }}
"""


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


class _CommandInput(TextArea):
    """带指令补全的多行输入框：内容超出终端宽度自动换行多行显示（TextArea），
    输入 / 开头的指令前缀时，在输入框上方弹出候选 ListView
    （仅此场景可见），↑/↓ 选择、Tab/Enter 接受、Esc 关闭；普通消息 Enter 提交、
    Shift+Enter / Ctrl+J 换行（Shift+Enter 需终端支持 kitty 键盘协议，Ctrl+J 全终端可用）。

    按键全部在 _on_key 拦截：TextArea 的 _on_key 会消费 Enter（插换行）/Tab（缩进）/Esc，
    普通 BINDINGS 要等键未被消费、冒泡到 App 才检查，永远轮不到；拦截后再直接处理。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: list[str] = []
        self._hl = 0  # 高亮项在 _candidates 中的下标

    # ---------- 候选列表显隐与过滤 ----------

    def _suggest_list(self) -> Optional[ListView]:
        try:
            return self.screen.query_one("#cmd-suggest", ListView)
        except Exception:
            return None  # 尚未挂载

    def _refresh_suggestions(self) -> None:
        """按当前输入过滤候选；仅 / 前缀且未完整时显示列表，其余情况隐藏。"""
        lv = self._suggest_list()
        if lv is None:
            return
        value = self.text.strip()
        if value.startswith("/") and " " not in value:
            candidates = [c for c in _SLASH_COMMANDS if c.startswith(value) and c != value]
        else:
            candidates = []
        for item, cmd in zip(lv.children, _SLASH_COMMANDS):
            item.styles.display = "block" if cmd in candidates else "none"
        self._candidates = candidates
        self._hl = 0
        if candidates:
            lv.styles.display = "block"
            lv.index = _SLASH_COMMANDS.index(candidates[0])
        else:
            lv.styles.display = "none"
            lv.index = None

    def _sync_highlight(self) -> None:
        lv = self._suggest_list()
        if lv is not None and self._candidates:
            lv.index = _SLASH_COMMANDS.index(self._candidates[self._hl])

    # ---------- 键盘 ----------

    async def _on_key(self, event: events.Key) -> None:
        """拦截 Enter/换行/Tab 与候选导航键；其余交给 TextArea 编辑。
        换行键组：shift+enter / ctrl+enter 需终端支持 kitty 键盘协议才可分（如 WezTerm、
        Linux 原生终端）；ctrl+j 发送 LF，与 Enter 的 CR 在所有终端都区分，是通用兜底。"""
        key = event.key
        newline_keys = ("shift+enter", "ctrl+j", "ctrl+enter")
        if key in ("enter", "tab", *newline_keys) or (key in ("up", "down", "escape") and self._candidates):
            event.stop()
            event.prevent_default()
            if key == "enter":
                if self._candidates:
                    self.text = self._candidates[self._hl]
                    self.cursor_location = (0, len(self.text))
                self.post_message(Input.Submitted(self, self.text))
            elif key == "tab" and self._candidates:
                self.text = self._candidates[self._hl]
                self.cursor_location = (0, len(self.text))
            elif key in newline_keys:
                self.insert("\n")
            elif key == "up":
                self._hl = (self._hl - 1) % len(self._candidates)
                self._sync_highlight()
            elif key == "down":
                self._hl = (self._hl + 1) % len(self._candidates)
                self._sync_highlight()
            else:  # escape：关闭补全
                self._candidates = []
                lv = self._suggest_list()
                if lv is not None:
                    lv.styles.display = "none"
                    lv.index = None
            return
        await super()._on_key(event)

    # ---------- 事件 ----------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        self._refresh_suggestions()



class ChatApp(App):
    """Nano-Harness 对话主界面：标题栏 + 卡片式聊天记录 + 状态行 + 输入框。

    handle_query(query): 每轮用户消息回调，在后台线程执行（可调用本模块渲染函数）。
    session_manager: 可选，提供 new_session / delete_session / load_session_list /
                     load_session / add_message / current_session 的对象（如 core.session 的 SESSION_MANAGER）。
    """

    CSS = _APP_CSS
    TITLE = "Nano-Harness"
    SUB_TITLE = "Textual UI"

    def __init__(self, handle_query: Optional[Callable[[str], None]] = None,
                 session_manager: Any = None,
                 banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE)) -> None:
        super().__init__()
        self._handle = handle_query
        self._manager = session_manager
        self._banner = banner
        self._busy = False
        self._pinned = True
        self._active_stream: Optional[Static] = None
        self._status_text = ""
        self._status_spin = False  # 当前状态文本前是否轮播加载动画
        self._spin_cursor = 0
        self._status_interval: Any = None  # 动画 interval 句柄（首次出现动画状态时惰性启动）
        self._perm_pending = False
        self._perm_holder: dict[str, Any] = {}
        self._perm_done: Optional[threading.Event] = None

    # ---------- 基础部件 ----------

    def compose(self) -> ComposeResult:
        title, subtitle = self._banner
        with Vertical():
            yield Static(_markup(f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]", justify="center"),
                         id="titlebar", markup=False)
            yield VerticalScroll(id="chat")
            with Vertical(id="dock"):  # 底部固定区：状态行 + 指令补全列表（按需显示）+ 输入框
                yield Static("", id="status")
                yield ListView(*(ListItem(Label(cmd)) for cmd in _SLASH_COMMANDS), id="cmd-suggest")
                with Horizontal(id="inputbar"):  # 上下粗实线输入条：>> 前缀 + 输入框
                    yield Static(">> ", id="prompt-mark")
                    yield _CommandInput(placeholder=_PLACEHOLDER, id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", TextArea).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """点击候选列表项 = 接受该指令"""
        event.stop()
        idx = event.list_view.index
        if idx is None or not 0 <= idx < len(_SLASH_COMMANDS):
            return
        prompt = self.query_one("#prompt", _CommandInput)
        cmd = _SLASH_COMMANDS[idx]  # 子项与 _SLASH_COMMANDS 一一对应（见 compose）
        prompt.text = cmd
        prompt.cursor_location = (0, len(cmd))
        prompt.focus()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _status(self) -> Static:
        return self.query_one("#status", Static)

    def _prompt(self) -> TextArea:
        return self.query_one("#prompt", TextArea)

    # ---------- 滚动跟随：流式更新时钉在底部，用户上滚后取消 ----------

    def _scroll_pinned(self) -> None:
        if not self._pinned:
            return
        chat = self._chat()

        def _go() -> None:
            try:
                chat.scroll_end(animate=False, immediate=True)
            except Exception:
                pass

        _go()
        self.call_after_refresh(_go)  # 布局完成后校正一次

    def _on_mouse_scroll_up(self, event) -> None:
        # ponytail: 仅跟踪鼠标滚轮；键盘滚动/翻页不解除钉底，实际影响很小
        if event.control is self._chat():
            self._pinned = False
            event.stop()

    def _on_mouse_scroll_down(self, event) -> None:
        if event.control is self._chat():
            chat = self._chat()
            self._pinned = chat.scroll_offset.y >= chat.max_scroll_y - 1
            event.stop()

    # ---------- 卡片 ----------

    def _add_card(self, kind: str, body: Any) -> Static:
        """追加一张卡片，返回 body Static（流式更新用）"""
        body_w = Static(body, classes="card-body", markup=False)
        self._chat().mount(Vertical(body_w, classes=f"card {kind}"))
        self._scroll_pinned()
        return body_w

    # ---------- 状态文本（render_thinking_status / render_tool_calling_status 共用） ----------

    def _set_status_text(self, text: str, spin: bool = False) -> None:
        """设置状态文本；spin=True 时由 interval 在文本前轮播加载动画帧（帧不存入 _status_text）"""
        self._status_text = text
        self._status_spin = spin
        self._spin_cursor = 1  # 首帧立即渲染，interval 从第 2 帧继续
        self._status().update(f"{_SPINNER_FRAMES[0]} {text}" if spin else text)
        self._sync_spinner()

    def _sync_spinner(self) -> None:
        """动画状态确保 interval 运行，静态状态停掉 interval（同一时刻至多一个驱动者，无竞态）"""
        if self._status_spin and self._status_interval is None:
            self._status_interval = self.set_interval(0.1, self._tick_status)
        elif not self._status_spin and self._status_interval is not None:
            self._status_interval.stop()
            self._status_interval = None

    def _tick_status(self) -> None:
        frame = _SPINNER_FRAMES[self._spin_cursor % len(_SPINNER_FRAMES)]
        self._spin_cursor += 1
        self._status().update(f"{frame} {self._status_text}")

    def _status_swap(self, text: str, spin: bool = False) -> tuple[str, bool]:
        """设置状态并返回旧状态 (文本, 是否动画)；状态上下文从不嵌套，退出时恢复旧状态即可"""
        previous = (self._status_text, self._status_spin)
        self._set_status_text(text, spin=spin)
        return previous

    # ---------- 流式回复 ----------

    def _end_scope(self) -> None:
        self._active_stream = None

    def _stream_update(self, text: str) -> None:
        if self._active_stream is None:
            self._active_stream = self._add_card("assistant", RichMarkdown(""))
        # ponytail: 每 chunk 整篇重渲 Markdown（O(n²)），与原 ui.py Live 实现同级；长文变慢时改增量渲染
        self._active_stream.update(RichMarkdown(text))
        self._scroll_pinned()

    # ---------- 用户输入回合 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self._prompt().text = ""
        if not query and not self._perm_pending:
            return
        if self._perm_pending:
            # 输入框正处于权限确认状态：本次输入即回答（与旧版命令行一致：输入条复用）
            self._perm_holder["value"] = query
            self._perm_done.set()
            self._perm_pending = False
            self._prompt().disabled = True
            self._set_status_text("Working…", spin=True)
            return
        self._pinned = True
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/clear":
            self._clear_cards()
        elif cmd == "/new":
            if self._manager is not None:
                try:
                    self._manager.new_session()
                except Exception as exc:
                    render_background_notification(f"新建会话失败：{exc}", title="⚠️ Session Error")
            self._clear_cards()
        elif cmd == "/sessions":
            self._open_sessions()
        elif self._busy:
            render_background_notification("上一轮仍在运行，请稍候…", title="⏳ Busy")
        else:
            self._busy = True
            self._prompt().disabled = True
            self._set_status_text("Working…", spin=True)
            render_user_input(query)
            threading.Thread(target=self._turn_worker, args=(query,), daemon=True,
                             name="agent-turn").start()

    def _turn_worker(self, query: str) -> None:
        try:
            if self._manager is not None:
                try:
                    self._manager.add_message({"role": "user", "content": query})
                except Exception as exc:
                    render_background_notification(f"保存会话消息失败：{exc}", title="⚠️ Session Error")
            try:
                (self._handle or _demo_agent)(query)
            except Exception as exc:
                render_background_notification(f"{type(exc).__name__}: {exc}", title="⚠️ Agent Error")
        finally:
            try:
                self.call_from_thread(self._set_idle)
            except RuntimeError:
                pass  # App 已退出

    def _set_idle(self) -> None:
        self._busy = False
        self._perm_pending = False
        self._set_status_text("")
        prompt = self._prompt()
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = False
        prompt.focus()

    # ---------- 会话 ----------

    def _open_sessions(self) -> None:
        if self._manager is None:
            render_background_notification("未接入 SessionManager，/sessions 不可用", title="⚠️ Sessions")
            return
        sessions = self._manager.load_session_list()
        if not sessions:
            render_sessions()  # 空列表提示卡片
            return
        self.push_screen(SessionPickerScreen(self._manager), callback=self._on_session_picked)

    def _on_session_picked(self, result: tuple[Optional[str], bool]) -> None:
        session_id, was_empty = result
        if not session_id:
            if was_empty:
                render_sessions()
            return
        loaded = self._manager.load_session(session_id)
        if loaded is not None:
            self._clear_cards()
            render_session_history(loaded)

    # ---------- 权限确认（内联卡片 + 复用输入条，避免跨线程推屏挂载竞态；
    # ponytail: 同一时刻仅一个权限请求（agent 单线程），并发请求需改请求队列） ----------

    def _begin_permission(self, message: str, prompt_str: str) -> None:
        self._add_card("perm", _markup(f"[bold #fef3c7]{escape(message)}[/bold #fef3c7]"))
        self._perm_pending = True
        prompt = self._prompt()
        prompt.placeholder = prompt_str.strip() or "Allowed? [y/N]"
        prompt.disabled = False
        prompt.focus()
        self._set_status_text("🔒 等待权限确认：输入 y/yes 允许，Enter/其它 拒绝")

    def _clear_cards(self) -> None:
        self._chat().remove_children()
        self._active_stream = None
        self._pinned = True


# ======================================================================
# 模块级桥接：渲染函数可在任意线程调用（App 线程内直连，其余桥接进事件循环）
# ======================================================================

_APP: Optional[ChatApp] = None


def _exec(fn: Callable, *args, **kwargs) -> Any:
    """在 App 线程中执行 fn；调用方线程为 App 线程时直接执行。"""
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI 未运行：请先调用 run()（或自行 ChatApp().run()）再执行渲染函数")
    if threading.get_ident() == app._thread_id:
        return fn(*args, **kwargs)
    try:
        return app.call_from_thread(fn, *args, **kwargs)
    except RuntimeError:
        return None  # App 退出竞态


def _require_worker_thread() -> None:
    app = _APP
    if app is not None and threading.get_ident() == app._thread_id:
        raise RuntimeError("ask_permission 必须在非 App 线程调用（如 handle_query 回调内部）")


def run(handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Any = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE)) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    不传 handle_query 时使用内置演示 Agent（流式 Markdown + 工具调用 + 权限确认等全流程演示，
    query 以 "sudo " 开头会触发权限确认）。
    传入 session_manager（如 core.session 的 SESSION_MANAGER）后 /new /sessions 可用。
    """
    global _APP
    app = ChatApp(handle_query=handle_query, session_manager=session_manager, banner=banner)
    _APP = app
    try:
        app.run()
    finally:
        _APP = None


# ---------------- 渲染函数（签名与 core/tui/ui.py 对应项一致） ----------------

def render_user_input(user_text: str) -> None:
    def _draw(app: ChatApp) -> None:
        app._add_card("user", _markup(f"[bold #f9fafb]{escape(user_text)}[/bold #f9fafb]"))
    _exec(_draw, _APP)


def render_tool_call(tool_name: str, tool_args: Any) -> None:
    try:
        args_str = json.dumps(tool_args, ensure_ascii=False, indent=2) if isinstance(tool_args, (dict, list)) \
            else str(tool_args)
    except Exception:
        args_str = str(tool_args)
    body = _markup(f"[bold #fde68a]Tool:[/bold #fde68a] [bold white]{escape(tool_name)}[/bold white]\n"
                   f"[dim #e2e8f0]Args:\n{escape(args_str)}[/dim #e2e8f0]")

    def _draw(app: ChatApp) -> None:
        app._add_card("tool", body)
    _exec(_draw, _APP)


def render_tool_result(output: Any, max_lines: int = 12) -> None:
    """工具输出卡片：超过 max_lines 的行折叠为 "... [truncated N lines]"。
    输出与截断提示都经 escape() 再嵌入标记：方括号会被 Rich 解析成样式标签，
    不转义会导致渲染期 MissingStyle（如 style 'truncated 8 lines'）崩溃。"""
    output_str = str(output)
    lines = output_str.splitlines()
    if len(lines) > max_lines:
        shown = escape("\n".join(lines[:max_lines]))
        body = _markup(f"[dim #e2e8f0]{shown}[/dim #e2e8f0]\n"
                       f"[dim yellow]{escape(f'... [truncated {len(lines) - max_lines} lines]')}[/dim yellow]")
    else:
        body = _markup(f"[dim #e2e8f0]{escape(output_str)}[/dim #e2e8f0]")

    def _draw(app: ChatApp) -> None:
        app._add_card("result", body)
    _exec(_draw, _APP)


def render_tool_result_diff(rows: list[tuple[str, int, str]]) -> None:
    width = max(len(str(n)) for _, n, _ in rows)
    styled = [Text(f"{kind}{n:>{width}} │ {line}",
                   style={"+": "green", "-": "red", " ": "dim"}.get(kind, "dim")) for kind, n, line in rows]
    content = Text("\n").join(styled)

    def _draw(app: ChatApp) -> None:
        app._add_card("result", content)
    _exec(_draw, _APP)


def render_background_notification(message: str, title: str = "🔔 Background Task") -> None:
    # title 保留仅为与 ui.py 签名一致（agent_loop 以关键字传入）；卡片不再渲染标题行
    def _draw(app: ChatApp) -> None:
        app._add_card("notice", _markup(f"[dim #e2e8f0]{escape(message)}[/dim #e2e8f0]"))
    _exec(_draw, _APP)


def render_sessions() -> None:
    """空会话提示卡片（非空列表的展示与选择在 SessionPickerScreen）"""
    def _draw(app: ChatApp) -> None:
        app._add_card("sessions", _markup("[dim #e2e8f0]暂无会话[/dim #e2e8f0]"))
    _exec(_draw, _APP)


def render_session_history(session) -> None:
    """按消息顺序重放会话历史：用户 / 工具调用 / 工具结果 / 助手回复"""
    for message in session.messages:
        if message.role == "user":
            render_user_input(message.content)
        elif message.role == "assistant":
            for tool_call in (message.tool_calls or []):
                fn = tool_call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", ""))
                except (TypeError, ValueError):
                    args = fn.get("arguments", "")
                render_tool_call(fn.get("name", "tool"), args)
            if message.content:
                render_assistant_response(message.content)
        elif message.role == "tool":
            render_tool_result(message.content)


@contextmanager
def render_scope():
    """标记一轮流式输出的结束（App 内卡片常驻，无需二次静态打印）"""
    try:
        yield
    finally:
        _exec(lambda app: app._end_scope(), _APP)


def stream_assistant_response(accumulated_text: str = "") -> None:
    """流式更新当前 Assistant 卡片（首次调用自动建卡）"""
    if not accumulated_text:
        return
    _exec(lambda app: app._stream_update(accumulated_text), _APP)


def render_assistant_response(content: str) -> None:
    """渲染一张静态 Assistant Markdown 卡片"""
    if not content:
        return
    _exec(lambda app: app._add_card("assistant", RichMarkdown(content)), _APP)


@contextmanager
def _status_context(text: str):
    """状态行加载动画上下文：进入后 text 前轮播 spinner 帧（由 App 内 interval 驱动），
    退出后恢复进入前的状态（文本 + 是否动画），busy「处理中…」动画因此无缝续播。"""
    previous = _exec(lambda app: app._status_swap(text, spin=True), _APP)
    try:
        yield
    finally:
        if previous is not None:
            _exec(lambda app: app._set_status_text(*previous), _APP)


def render_thinking_status(message: str = "Thinking..."):
    """返回状态行上下文管理器：spinner 加载动画 · 思考中"""
    return _status_context(message)


def render_tool_calling_status(message: str):
    """返回状态行上下文管理器：spinner 加载动画 · 工具执行中"""
    return _status_context(message)


def ask_permission(message: str, prompt_str: str = "  Allowed? [y/N] ") -> str:
    """渲染权限确认卡片并阻塞等待回答（只能在非 App 线程调用），返回原始输入文本"""
    _require_worker_thread()
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI 未运行：请先调用 run()")
    done = threading.Event()
    holder: dict[str, Any] = {"value": ""}

    def _ask() -> None:
        app._perm_holder = holder
        app._perm_done = done
        app._begin_permission(message, prompt_str)

    try:
        _exec(_ask)
        done.wait()
    except Exception:
        return ""
    return holder["value"]


# ======================================================================
# 内置演示：无 handle_query 时的可交互 Demo（对齐原 ui.py __main__ 的演示范围）
# ======================================================================

def _demo_agent(query: str) -> None:
    with render_thinking_status():
        time.sleep(0.6)
    chunks = [f"## 这是针对【{query}】的回答：\n",
              "### 1. Textual UI 重构成功\n",
              "### 2. 流式 Markdown 卡片渲染\n\n```python\nprint('Hello nano-harness')\n```\n",
              "> 演示环境无需 API Key，接入真实 Agent 请调用 `run(handle_query=..., session_manager=...)`"]
    with render_scope():
        accumulated = ""
        for chunk in chunks:
            time.sleep(0.16)
            accumulated += chunk
            stream_assistant_response(accumulated)
    render_tool_call("bash", {"command": "ls -la"})
    with render_tool_calling_status("bash({'command': 'ls -la'})"):
        time.sleep(0.6)
    render_tool_result("total 0\n-rw-r--r-- 1 user user 1234 demo.txt\n（演示输出）")
    render_tool_result_diff([(" ", 1, "def main():"), ("-", 2, "    print('old')"),
                             ("+", 2, "    print('new')")])
    render_background_notification("后台任务 task-001 已完成（演示）", title="🔔 Background Task")
    if query.startswith("sudo"):
        answer = ask_permission(f"检测到高风险指令，是否允许执行？\n\n{query}")
        render_assistant_response(f"**权限选择**：`{answer or '(拒绝)'}`")


if __name__ == "__main__":
    import asyncio
    import sys

    if "--smoke" in sys.argv:
        async def _smoke() -> None:
            app = ChatApp(handle_query=_demo_agent)
            global _APP
            _APP = app
            try:
                async with app.run_test() as pilot:
                    await pilot.pause(0.2)
                    prompt = app.query_one("#prompt", _CommandInput)
                    # 输入框宽度应占满终端宽度（与 terminal 一致，不随内容变化）
                    assert prompt.size.width >= app.size.width - 10, f"输入框宽度未占满终端: {prompt.size.width}"
                    # 长文本超过终端宽度应自动换行、多行加高（height:auto）
                    prompt.text = "y" * 300
                    await pilot.pause(0.05)
                    assert prompt.size.height >= 4, f"长文本未多行加高: {prompt.size.height}"
                    prompt.text = "hello textual"
                    await pilot.press("enter")
                    for _ in range(300):  # 最多 ~15s 等演示回合结束
                        await pilot.pause(0.05)
                        if not app._busy:
                            break
                    assert not app._busy, "回合未结束"
                    render_tool_result("line\n" * 15)  # 触发截断提示（含方括号），回归渲染期 MissingStyle
                    for _ in range(10):
                        await pilot.pause(0.05)
                    assert app._exception is None, f"渲染异常: {app._exception}"
                    cards = list(app.query("#chat .card"))
                    assert len(cards) >= 5, f"卡片数量不足: {len(cards)}"
                    # 消息卡片（画板）宽度应与终端一致
                    assert cards[0].region.width >= app.size.width, f"卡片宽度未占满终端: {cards[0].region.width}"
                    assert any("assistant" in c.classes for c in cards), "缺少 Assistant 卡片"
                    chat = app.query_one("#chat", VerticalScroll)
                    # 卡片 height:auto 后内容超出视口才可滚动；回归 1fr 均分时 max_scroll_y 恒为 0
                    assert chat.max_scroll_y >= 1, "卡片未按内容自适应高度，消息列表不可滚动"
                    # 指令补全：/ 前缀输入时输入框下方弹出候选 ListView（仅此时可见），↑/↓+Tab/Enter 接受；其它输入不显示
                    prompt.focus()
                    suggest = app.query_one("#cmd-suggest", ListView)
                    prompt.text = "/"
                    for _ in range(10):
                        await pilot.pause(0.02)
                    assert suggest.styles.display != "none", "输入 / 未弹出候选 ListView"
                    assert prompt._candidates == ["/exit", "/clear", "/new", "/sessions"], prompt._candidates
                    visible = [c for c in suggest.children if c.styles.display != "none"]
                    assert len(visible) == 4, f"候选条数错误: {len(visible)}"
                    await pilot.press("down")
                    await pilot.press("down")
                    await pilot.press("tab")
                    assert prompt.text == "/new", f"Tab 接受高亮失败: {prompt.text!r}"
                    for _ in range(5):
                        await pilot.pause(0.02)
                    assert suggest.styles.display == "none", "完整指令后列表应隐藏"
                    prompt.text = "/s"
                    for _ in range(10):
                        await pilot.pause(0.02)
                    assert prompt._candidates == ["/sessions"], prompt._candidates
                    visible = [c for c in suggest.children if c.styles.display != "none"]
                    assert len(visible) == 1, f"过滤候选条数错误: {len(visible)}"
                    await pilot.press("enter")  # Enter 应用高亮项并提交
                    for _ in range(20):
                        await pilot.pause(0.02)
                    assert not app._busy, "Enter 应提交补全后的 /sessions（走会话分支，不起 agent 回合）"
                    bodies = [str(w.render()) for w in app.query(".card-body")]
                    assert any("未接入 SessionManager" in b for b in bodies), "Enter 未提交 /sessions"
                    prompt.text = "/xyz"
                    for _ in range(10):
                        await pilot.pause(0.02)
                    assert suggest.styles.display == "none", "无匹配不应显示列表"
                    prompt.text = "hello world"
                    for _ in range(10):
                        await pilot.pause(0.02)
                    assert suggest.styles.display == "none", "普通消息不应显示列表"
                    print(f"[smoke] OK, {len(cards)} cards rendered, scrollable, listview completion OK")

                    # 会话选择弹窗：大量会话时弹窗不得超出屏幕/裁剪列表，↓ 可滚动到最后一个会话
                    class _FakeSession:
                        def __init__(self, i: int):
                            self.id = f"session-{i:06d}.jsonl"
                            self.title = f"会话标题 {i}"
                            self.timestamp = f"2025-01-01 {10 + i // 60:02d}:{i % 60:02d}:{i % 60:02d}"

                    class _FakeManager:
                        current_session = "session-000001.jsonl"
                        sessions = [_FakeSession(i) for i in range(150)]

                        def load_session_list(self):
                            return list(self.sessions)

                        def delete_session(self, sid):
                            self.sessions = [s for s in self.sessions if s.id != sid]

                        def load_session(self, sid):
                            return next((s for s in self.sessions if s.id == sid), None)

                    app.push_screen(SessionPickerScreen(_FakeManager()))
                    for _ in range(10):
                        await pilot.pause(0.02)
                    scr = app.screen_stack[-1]
                    olist = scr.query_one("#sess-list", OptionList)
                    picker = olist.parent
                    # 回归：picker 曾 height:auto+max-height 搭配 Center(height:auto)，小终端下整体溢出屏幕、
                    # 列表底部被裁剪（overflow:hidden），后半段会话永远不可见/不可达
                    assert scr.region.contains_region(picker.region), \
                        f"弹窗超出屏幕: {picker.region} / {scr.region}"
                    assert olist.region.bottom <= picker.region.bottom, \
                        f"会话列表被弹窗裁剪: {olist.region} / {picker.region}"
                    assert olist.option_count == 150
                    # 排序：新到旧；每行时间戳顶到行最右（回归：曾无时间戳列/左对齐）
                    order = sorted(_FakeManager.sessions, key=lambda s: s.timestamp, reverse=True)
                    from rich.cells import cell_len
                    from textual.style import Style as TStyle
                    for pos in range(150):
                        opt = olist.get_option_at_index(pos)
                        assert opt.id == order[pos].id, f"第 {pos} 行排序错误: {opt.id}"
                        row = olist._get_option_render(opt, TStyle())[0]
                        assert row.cell_length == olist.scrollable_content_region.width
                        # 展示到分钟（去秒），秒仅用于排序
                        shown = order[pos].timestamp[:16]
                        assert row.text.rstrip().endswith(shown), row.text[-25:]
                    for _ in range(149):
                        await pilot.press("down")
                    await pilot.pause(0.05)
                    assert olist.highlighted == 149, f"↓ 无法到达最后一个会话: {olist.highlighted}"
                    newest_last = sorted(_FakeManager.sessions, key=lambda s: s.timestamp, reverse=True)[-1]
                    assert olist.get_option_at_index(149).id == newest_last.id
                    await pilot.press("escape")
                    for _ in range(10):
                        await pilot.pause(0.02)
                    assert app._exception is None, f"渲染异常: {app._exception}"
                    print("[smoke] sessions picker OK: 150 sessions, all reachable, no clipping")
            finally:
                _APP = None

        asyncio.run(_smoke())
    else:
        run()
