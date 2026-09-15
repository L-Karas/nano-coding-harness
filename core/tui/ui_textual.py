"""
Textual 版终端界面（core/tui/ui.py 的 Textual 重构，独立新模块，未改动原代码）

与原 Rich+prompt_toolkit 版不同：本模块是一个完整的 Textual 全屏 App，
聊天历史、流式 Markdown、工具卡片、会话切换、权限确认都在同一个事件循环里，
不再依赖 Live 原地重绘 / prompt_toolkit 行输入。

模块划分（按逻辑边界拆自本文件，见各模块 docstring）：
- theme.py    主题常量（标题文案 / 动画帧 / _markup 安全解析）
- app.css     全局样式表（原 theme._APP_CSS，ChatApp 经 CSS_PATH 加载）
- widgets.py  部件：会话选择弹窗、带 / 指令与 @ 文件补全的输入框
- panels.py   主界面分区部件：标题栏 / 聊天画板 / 底部停靠区
- render.py   线程安全渲染 API：任意线程可调，驱动 ChatApp 内部方法
- demo.py     离线演示 Agent（--smoke 自检用）
- smoke.py    --smoke 冒烟自检（原 __main__ 内联，拆出以保持本模块精简）
- utils.py    终端环境信息与指令表（原有）

本模块保留：ChatApp（compose 只留左右分栏骨架，分区部件见 panels.py）、
run() 入口，以及 `python -m core.tui.ui_textual` 启动（--smoke 自检见 smoke.py）。

不传 handle_query 时使用 core.loop_with_interrupt 的真实 agent 回合（AgentRuntime +
cron 自动回合 + MCP 预热，见 _default_agent_turn）。

接入自有 Agent:
    from core.tui.ui_textual import run
    run(handle_query=agent_turn, session_manager=SESSION_MANAGER)
    # agent_turn(query) 在后台线程执行，须阻塞至回合结束（UI 靠它判断忙碌态并上屏错误）；
    # 内部渲染调用 core.tui.render 的同名函数（线程安全）。

线程模型:
    - App 事件循环跑主线程；每轮用户输入在独立后台线程里调用 handle_query。
    - 渲染函数（core.tui.render）可从任意线程调用（App 线程内直接执行，
      其它线程经 call_from_thread 桥接）。
    - ask_permission 只能在非 App 线程调用（会阻塞等待用户从停靠区 yes/no 列表作答）。
    - Esc：权限确认挂起时先拒绝（优先级最高）；确认结束后若回合仍在进行，才调 on_interrupt
      中断 agent，并 notify「User interrupted」（默认回合的 on_interrupt 即 AgentRuntime.interrupt，
      见 _default_agent_turn）。
"""

from __future__ import annotations

import asyncio
import signal
import threading
from typing import Any, Callable, Optional

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Input, Markdown, OptionList, Static, TextArea
from textual.widgets.markdown import MarkdownStream

import core.tui.render as _render  # run() 期间把 ChatApp 实例挂到渲染桥接的 _APP 全局（见 run()）
from core import background_task as _bg  # 右栏 Background Tasks 数据源（模块引用，随 agent 线程写入实时可见）
from core.session.session import SessionManager
from core.skill import skills as _skills  # /skills 弹窗数据源（模块导入时 scan_skills() 扫描 .harness/skills）
from core.todo import todo as _todo  # 右栏 Todos 数据源（todo_write 整体替换 CURRENT_TODOS，须经模块取最新引用）
from core.tui.panels import _ChatBoard, _ChatDock, _TitleBar
from core.tui.render import (
    render_background_notification,
    render_sessions,
    render_session_history,
    render_user_input,
)
from core.tui.theme import (
    DEFAULT_SUBTITLE,
    DEFAULT_TITLE,
    _PLACEHOLDER,
    _SPINNER_FRAMES,
    _markup,
)
from core.tui.utils import current_git_branch, working_directory
from core.tui.widgets import (
    EffortScreen,
    ModelPickerScreen,
    ProviderScreen,
    SessionPickerScreen,
    SkillsScreen,
    _CommandInput,
)


class _ChatScreen(Screen):
    """主屏（左聊板 / 右栏 / 底部停靠区所在屏）：焦点始终留在输入栏。

    所有焦点变更都汇经 Screen.set_focus（Tab 切焦、点击可聚焦部件、弹窗关闭后的
    焦点还原），在此统一改道：目标不是 #prompt 时交回输入栏——输入信息无需先点输入框。
    例外：弹窗屏在顶（本屏非活动屏）、权限确认期间 #perm-list 持焦作答、
    输入栏禁用（回合执行中，目标按原样落下，回合结束 _set_idle 收回焦点）。
    """

    def set_focus(self, widget, scroll_visible=True, from_app_focus=False) -> None:
        if widget is not None and self.app.screen is self \
                and not getattr(self.app, "_perm_pending", False):
            try:
                prompt = self.query_one("#prompt")
            except NoMatches:  # 挂载早期 / 卸载期无输入栏：按原样落下
                pass
            else:
                if widget is not prompt and not prompt.disabled:
                    widget = prompt
        super().set_focus(widget, scroll_visible=scroll_visible, from_app_focus=from_app_focus)


class ChatApp(App):
    """Nano-Harness 对话主界面：标题栏 + 卡片式聊天记录 + 状态行 + 输入框。

    handle_query(query): 每轮用户消息回调，在后台线程执行（可调用本模块渲染函数）；
                         不传则用真实 agent 回合（见 _default_agent_turn）。
    on_interrupt(): 中断当前回合的回调（Esc，权限确认优先）；handle_query 为默认时由 runtime 提供。
    session_manager: 可选，提供 new_session / delete_session / load_session_list /
                     load_session / add_message / current_session 的对象（如 core.session 的 SESSION_MANAGER）。
    """

    CSS_PATH = "app.css"  # 同目录样式表，路径相对本模块文件
    TITLE = "Nano-Harness"
    SUB_TITLE = "Textual UI"

    def get_default_screen(self) -> Screen:
        """默认屏用 _ChatScreen：set_focus 时把焦点始终押回输入栏（见该类 docstring）。"""
        return _ChatScreen(id="_default")

    # ctrl+c 覆盖 Textual App 默认的 help_quit（系统绑定）：Ctrl+C 不再提示/退出，退出请用
    # /exit 或 ctrl+q（后者保留自 App 默认绑定）。复制保留：有选中文本时 ctrl+c 先经屏层
    # copy_text 复制（输入框选区 / 鼠标选中的卡片文本），无选区才落到下方动作的空操作分支。
    BINDINGS = [("escape", "deny_permission", "Deny/Interrupt"),  # 权限确认优先，其次中断进行中的回合
                ("ctrl+c", "copy_or_ignore", "Ignore")]

    def action_copy_or_ignore(self) -> None:
        """Ctrl+C：有选中文本则复制（保留窗口复制），否则空操作（不提示不退出）。
        覆盖基类 help_quit。有选区时屏层 copy_text 绑定更内层会先复制，
        通常到不了这里；无选区时复制分支查不到文本，等价于原 ignore。"""
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)

    def __init__(self, handle_query: Optional[Callable[[str], None]] = None,
                 session_manager: SessionManager = None,
                 banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
                 on_interrupt: Optional[Callable[[], None]] = None) -> None:
        # ansi_color=True：禁用 ANSI→真彩色 过滤器（否则 ansi_default 会被映射成
        # Textual 内置 monokai 底色 #0c0c0c 而非 SGR 49，整体背景≈纯黑，与终端背景不一致）
        super().__init__(ansi_color=True)
        if handle_query is None:
            handle_query, on_interrupt = _default_agent_turn()
        self._handle = handle_query
        self._interrupt = on_interrupt
        self._manager = session_manager
        self._banner = banner
        self._busy = False
        self._active_stream: Optional[Markdown] = None
        self._markdown_stream: Optional[MarkdownStream] = None  # 流卡片增量渲染句柄（get_stream 后台合并解析）
        self._status_text = ""
        self._status_spin = False  # 当前状态文本前是否轮播加载动画
        self._spin_cursor = 0
        self._status_interval: Any = None  # 动画 interval 句柄（首次出现动画状态时惰性启动）
        self._right_cursor = 0  # 右栏列表加载动画帧下标（Todos in_progress / Bg running 同相轮播，_tick_right_anim 推进）
        self._right_interval: Any = None  # 右栏加载动画 interval（出现进行中项时惰性启动，见 _sync_right_anim）
        self._right_open: dict[str, set[str]] = {}  # 各区已展开的条目 id（数据重建后按 id 延续展开态）
        self._right_sig: dict[str, Any] = {}  # 各区上次渲染的数据签名（内容/状态/顺序未变则跳过行重建）
        self._perm_pending = False
        self._perm_holder: dict[str, Any] = {}
        self._perm_done: Optional[threading.Event] = None

    # ---------- 基础部件 ----------

    def compose(self) -> ComposeResult:
        title, subtitle = self._banner
        with Horizontal(id="main"):  # 左右分栏：左 4fr 现有聊天界面，右 1fr 预留（比例见 app.css）
            with Vertical(id="left"):
                yield _TitleBar(title, subtitle)
                yield _ChatBoard(id="chat")
                yield _ChatDock(id="dock")
            with Vertical(id="right"):  # 右栏信息面板；折叠时整体隐藏（聊天区吃满全宽），仅右缘 ▸ 标签可点
                yield Static("▼ Information", id="right-title")
                # 两个分区卡片（Todos / Background Tasks）：标题行（▼/▶）点击
                # 独立折叠/展开，圆角边框随折叠态包裹——折叠时只包标题、展开时包标题 + 内容列表
                # （-collapsed 类挂在分区容器 #key-section 上，列表随 CSS 隐藏；数据由
                # _refresh_info_lists 按 1s 轮询同步 core.todo / core.background_task）
                for _key, _label in (("todos", "Todos"), ("bg", "Background Tasks")):
                    with Vertical(id=f"{_key}-section"):
                        head = Static(f"▼ {_label}", id=f"{_key}-head")
                        head._key = _key  # 分区键（todos/bg），供计数与列表联动查询
                        head._label = _label
                        yield head
                        yield VerticalScroll(id=f"{_key}-list")  # 条目行由 _sync_rows 按数据源重建
        with Vertical(id="info-tab"):  # 折叠态展开标签：仅 ▸ 字形，dock 右侧垂直居中（点击展开）
            yield Static("▸", id="info-tab-glyph")

    def on_click(self, event: events.Click) -> None:
        """右栏标题 / 折叠标签点击：折叠时右栏整体隐藏、聊天区吃满全宽，右缘标签可点回；
        右栏分区标题行（Todos / Background Tasks）点击：该分区列表独立折叠/展开；
        可展开卡片正文与右栏条目行（-expandable 标记）点击：截断 ↔ 完整内容"""
        target = event.widget
        if target.has_class("-expandable"):  # 截断卡片正文 / 右栏 ▸/▾ 条目行（标记见 _add_card / _make_row）
            self._toggle_expand(target)
            return
        if target.id in ("todos-head", "bg-head"):  # 右栏分区标题行：折叠/展开对应列表
            self._toggle_info_section(target)
            return
        if target.id not in ("right-title", "info-tab-glyph"):
            return
        right = self.query_one("#right", Vertical)
        collapsed = not right.has_class("-collapsed")
        right.set_class(collapsed, "-collapsed")
        self.query_one("#info-tab", Vertical).set_class(collapsed, "-show")
        self.query_one("#right-title", Static).update(("▶ " if collapsed else "▼ ") + "Information")

    def on_mount(self) -> None:
        self._prompt().focus()
        self._chat().anchor()  # 钉底：溢出时新内容由 compositor 布局自动保持贴底；用户上滚解除、滚回底部自动恢复（机制见 panels._ChatBoard docstring）
        self._refresh_footer()
        # 首帧布局完成后补刷：on_mount / on_resize 时 footer 宽度还是 0，模型段会被当放不下
        # 而丢弃（gap 为负），等首个 2s 轮询才出现 → 右下角模型信息延迟过大
        self.call_after_refresh(self._refresh_footer)
        self._refresh_info_lists()  # 右栏分区首帧数据
        self.set_interval(2.0, self._refresh_footer)  # 轮询刷新 cwd/git（用户可能另开终端切目录/分支）；单次 ~毫秒级
        self.set_interval(1.0, self._refresh_info_lists)  # 轮询右栏 Todos / Background Tasks（agent 线程写入）

    def on_resize(self, event: events.Resize) -> None:
        """终端尺寸变化时重算页脚右对齐（2s 轮询外保持对齐实时）"""
        self._refresh_footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """停靠区三个 OptionList 的选中（冒泡统一收口）：权限列表直接作答（yes/no 选项 id）；
        / 指令把选中项 id（= 指令串）填进输入；@ 文件把选中路径补全进输入（均不发送）。
        弹窗（/sessions /provider /model /skills）各自的 OptionList 在弹窗内已 stop，
        冒泡至此的按 id 过滤，只认停靠区三个列表。"""
        oid = event.option_list.id
        if oid == "perm-list":  # 选项 id 固定 yes/no（见 panels._ChatDock.compose）
            event.stop()
            self._answer_permission(event.option_id or "no")
            return
        prompt = self.query_one("#prompt", _CommandInput)
        if oid == "cmd-suggest":
            event.stop()
            cmd = event.option_id  # 行含别名括注时 id 仍是规范指令串
            prompt.text = cmd
            prompt.cursor_location = (0, len(cmd))
            prompt.focus()
        elif oid == "file-suggest":
            event.stop()
            if not prompt._apply_file_candidate(event.option_index):
                return
            prompt.focus()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _status(self) -> Static:
        return self.query_one("#status", Static)

    def _prompt(self) -> TextArea:
        return self.query_one("#prompt", TextArea)

    def _current_model_label(self) -> str:
        """当前模型信息串（页脚右端）：'(provider) model * level'；未配置 / 无 level 时无后缀。
        core.model 惰性导入：演示/冒烟环境未装 openai 等依赖时返回空串（页脚只显 cwd）。"""
        try:
            from core.client import shared_model_client
            client = shared_model_client()
        except Exception:
            return ""
        provider = getattr(client, "current_provider", "") or ""
        model = getattr(client, "current_model", "") or ""
        if not provider or not model:
            return ""
        level = getattr(client, "current_thinking_level", "") or ""
        return f"({provider}) {model}" + (f" * {level}" if level else "")

    def _refresh_footer(self) -> None:
        """刷新最底行文本：左 = 当前工作目录 (git 分支)，右 = 当前模型状态
        （'(provider) model * level'，同行暗灰右对齐；空间不足时只保留 cwd）"""
        cwd = working_directory()
        branch = current_git_branch()
        label = f"{cwd} ({branch})" if branch else cwd
        footer = self.query_one("#footer", Static)
        text = Text(label, no_wrap=True, style="#64748b")
        model_label = self._current_model_label()
        if model_label:
            model = Text(model_label, no_wrap=True, style="#64748b")  # 右对齐段（与 cwd 同色）
            gap = footer.content_region.width - text.cell_len - model.cell_len
            if gap >= 0:
                text.append(" " * gap)
                text.append_text(model)
        footer.update(text)

    # ---------- 右栏信息分区（Todos / Background Tasks 折叠卡片） ----------

    def _toggle_info_section(self, head: Static) -> None:
        """分区标题行（#todos-head / #bg-head）点击：▼ 展开 ↔ ▶ 折叠
        （-collapsed 类挂在分区卡片 #key-section 上：折叠时卡片高度收成只包标题行、
        内容列表随 CSS 隐藏并变暗；箭头与条目计数经 _set_head_text 重写）"""
        section = self.query_one(f"#{head._key}-section", Vertical)
        collapsed = not section.has_class("-collapsed")
        section.set_class(collapsed, "-collapsed")
        self._set_head_text(head, self._section_count(head._key))

    def _section_count(self, key: str) -> int:
        """分区条目实时计数（标题行 · n 后缀；bg 需锁内读取）"""
        if key == "bg":
            with _bg.BACKGROUND_LOCK:
                return len(_bg.BACKGROUND_TASKS)
        return len(_todo.CURRENT_TODOS)

    def _set_head_text(self, head: Static, count: int) -> None:
        """按所在分区卡片的折叠类还原箭头（▼/▶），并尾随 dim 条目计数（如 Todos · 2）"""
        suffix = f" [dim #a7bf21]· {count}[/dim #a7bf21]"
        glyph = "▶" if head.parent.has_class("-collapsed") else "▼"
        head.update(f"{glyph} {head._label}{suffix}")

    def _refresh_info_lists(self) -> None:
        """按模块级数据源同步两个分区（App 线程 1s 轮询，与 agent 线程写入解耦）：
        Todos = core.todo CURRENT_TODOS（todo_write 整体替换引用）；
        Background Tasks = core.background_task BACKGROUND_TASKS（bg 线程持锁改状态）。
        条目行按需重建（数据签名未变则跳过），动画帧由 _tick_right_anim 直接更新行文本。"""
        bg_items = self._bg_snapshot()  # 一次快照供行同步 / 计数 / 动画判定共用
        self._sync_right_rows(bg_items)
        counts = {"todos": len(_todo.CURRENT_TODOS),
                  "bg": len(bg_items)}
        for key in ("todos", "bg"):
            self._set_head_text(self.query_one(f"#{key}-head", Static), counts[key])
        self._sync_right_anim(bg_items)

    # ---------- 右栏条目行（每个条目 = 一条 ▸ 折叠摘要 ↔ ▾ 展开完整内容的行） ----------

    _ROW_EMPTY = {"todos": "No todos", "bg": "No background tasks"}
    _ROW_CLIP = {"todos": 72, "bg": 48}  # 折叠摘要单行字符上限；超出 / 多行截断附 …，展开可见全文

    def _sync_right_rows(self, bg_items: Optional[list] = None) -> None:
        """两区条目行同步（数据源见 _refresh_info_lists docstring；bg 快照须锁内拷贝，
        可复用调用方快照以免同帧两次取锁）"""
        if bg_items is None:
            bg_items = self._bg_snapshot()
        self._sync_rows("todos", self._todo_items())
        self._sync_rows("bg", self._bg_items(bg_items))

    def _todo_items(self) -> list[dict]:
        """Todos 条目行数据（list 快照：todo_write 可能随时整体替换 CURRENT_TODOS）"""
        return [{"id": todo.content, "sig": (todo.content, todo.status),
                 "content": todo.content, "status": todo.status}
                for todo in list(_todo.CURRENT_TODOS)]

    def _bg_items(self, tasks: list) -> list[dict]:
        """Background Tasks 条目行数据（快照由调用方锁内取好，结构见 core/background_task.py）"""
        return [{"id": bg_id, "sig": (bg_id, info.get("status"), info.get("tool_call", "")),
                 "call": info.get("tool_call", ""), "status": info.get("status")}
                for bg_id, info in tasks]

    def _sync_rows(self, kind: str, items: list[dict]) -> None:
        """按最新条目重建 #kind-list 的行：签名（内容/状态/顺序）与上次一致则跳过；
        变化时整列重建，且只保留仍存在的展开项 id（数据刷新不断开用户展开态）"""
        sig = tuple(it["sig"] for it in items)
        if sig == self._right_sig.get(kind):
            return
        self._right_sig[kind] = sig
        self._right_open[kind] = {it["id"] for it in items} & self._right_open.get(kind, set())
        holder = self.query_one(f"#{kind}-list", VerticalScroll)
        holder.remove_children()
        if not items:
            holder.mount(Static(self._ROW_EMPTY[kind], markup=False, classes="info-row"))  # 空列表占位（不可展开）
            return
        for it in items:
            holder.mount(self._make_row(kind, it))

    def _make_row(self, kind: str, it: dict) -> Static:
        """构建一条 ▸/▾ 条目行：_collapsed_body / _expand_builder 供 _toggle_expand 点击切换
        （同截断卡片机制，见 _add_card）；_kind/_data 供动画 tick 按最新帧重建文本"""
        open_ = it["id"] in self._right_open.get(kind, set())
        row = Static(self._row_text(kind, it, open_), markup=False, classes="info-row")
        row._kind, row._data = kind, it
        row._collapsed_body = self._row_text(kind, it, False)
        row._expand_body = None  # 完整正文缓存（同 _add_card：首次点击展开时构建）
        row._expand_builder = lambda: self._row_text(kind, it, True)
        row.add_class("-expandable")
        if open_:  # 数据刷新后按 id 延续展开态（正文已按展开态构建，类标记保证首次点击即收起）
            row.add_class("-expanded")
        return row

    def _row_text(self, kind: str, it: dict, expanded: bool) -> Text:
        """条目行文本：折叠 = 单行摘要；展开 = 摘要行 + 完整内容行（todo 全文 / 未截断 tool_call）。
        in_progress / running 行首为轮播帧字形，_tick_right_anim 按 _right_cursor 推进"""
        row = Text()
        row.append("▾ " if expanded else "▸ ", style="#64748b")  # 折叠态指示（分区标题 ▼/▶ 同族）
        if kind == "todos":
            glyph, glyph_color, text_style = {
                "pending": ("○", "#94a3b8", "#e2e8f0"),
                "in_progress": (_SPINNER_FRAMES[self._right_cursor % len(_SPINNER_FRAMES)], "#facc15", "#facc15"),
                "completed": ("●", "#4ade80", "strike #4ade80")}.get(it["status"],
                                                                     ("○", "#94a3b8", "#e2e8f0"))
            lines = it["content"].splitlines() or [""]
        else:  # bg：仅 running / completed 两态（见 core/background_task.py start_background_task）
            running = it["status"] == "running"
            glyph, glyph_color = ((_SPINNER_FRAMES[self._right_cursor % len(_SPINNER_FRAMES)], "#facc15")
                                  if running else ("●", "#4ade80"))
            text_style = "#facc15" if running else "strike #4ade80"
            lines = it["call"].splitlines() or [""]
        row.append(f"{glyph} {it['id']} " if kind == "bg" else f"{glyph} ", style=glyph_color)
        if expanded:
            row.append(lines[0], style=text_style)
            for line in lines[1:]:  # 多行内容：后续行缩进展示
                row.append(f"\n  {line}", style=text_style)
        else:
            row.append(lines[0][:self._ROW_CLIP[kind]], style=text_style)
            if len(lines[0]) > self._ROW_CLIP[kind] or len(lines) > 1:  # 折叠为单行摘要，其余展开可见
                row.append("…", style=text_style)
        return row

    def _bg_snapshot(self) -> list:
        """Background Tasks 快照（锁内拷贝；结构见 core/background_task.py start_background_task）"""
        with _bg.BACKGROUND_LOCK:
            return list(_bg.BACKGROUND_TASKS.items())

    def _sync_right_anim(self, bg_items: Optional[list] = None) -> None:
        """Todos 任一 in_progress 或后台任务任一 running 时，惰性启动 0.1s Braille spinner interval
        （与状态行动画同款；两栏共用游标同相轮播）；全部结束即停并复位帧（避免空闲期空转重绘）"""
        if bg_items is None:
            bg_items = self._bg_snapshot()
        active = (any(t.status == "in_progress" for t in list(_todo.CURRENT_TODOS))  # 快照：todo_write 随时整体替换
                  or any(info.get("status") == "running" for _, info in bg_items))
        if active and self._right_interval is None:
            self._right_interval = self.set_interval(0.1, self._tick_right_anim)
        elif not active and self._right_interval is not None:
            self._right_interval.stop()
            self._right_interval = None
            self._right_cursor = 0  # 复位首帧，下次有进行中项时从头轮播

    def _tick_right_anim(self) -> None:
        """轮播帧推进一档：running / in_progress 行按最新帧重建文本（顺带拾取两次 1s 轮询间
        todo_write / bg 线程的写入：数据签名变化即整列重建）；全部结束即停（无需等下次轮询）"""
        self._right_cursor += 1
        self._sync_right_rows()
        for row in self.query(".info-row.-expandable"):
            if row._data.get("status") in ("in_progress", "running"):
                row.update(self._row_text(row._kind, row._data, row.has_class("-expanded")))
        self._sync_right_anim()

    # ---------- 卡片 ----------

    def _add_card(self, kind: str, body: Any,
                  expand: Optional[Callable[[], Any]] = None) -> Static:
        """追加一张卡片，返回 body Static（流式更新用）。

        expand: 非 None 时正文可点击，在截断与完整内容间切换（builder 惰性构建完整正文，
        首次点击时执行并缓存；-expandable 类供 on_click 路由与指针样式）。"""
        body_w = Static(body, classes="card-body", markup=False)
        if expand is not None:
            body_w._collapsed_body = body
            body_w._expand_builder = expand
            body_w._expand_body = None  # 完整正文缓存（首次展开时构建）
            body_w.add_class("-expandable")
        self._chat().mount(Vertical(body_w, classes=f"card {kind}"))
        return body_w

    def _toggle_expand(self, body_w: Static) -> None:
        """截断卡片正文点击：截断 ↔ 完整内容（完整正文惰性构建一次后缓存）"""
        if body_w.has_class("-expanded"):  # 展开态: 点回收起截断正文
            body_w.update(body_w._collapsed_body)
            body_w.remove_class("-expanded")
            return
        if body_w._expand_body is None:  # 首次展开: 构建完整正文并缓存
            body_w._expand_body = body_w._expand_builder()
        body_w.update(body_w._expand_body)
        body_w.add_class("-expanded")

    def _add_markdown_card(self, content: str) -> Markdown:
        """追加一张 Assistant Markdown 卡片（链接可点击 → App.open_url 交给系统浏览器）"""
        md = Markdown(content, open_links=True)  # 块级内容 mount 后异步解析（后台线程 + 锁串行）
        self._chat().mount(Vertical(md, classes="card assistant"))
        return md

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

    def _run_stream_task(self, coro: Any, what: str) -> None:
        """把流协程挂到当前事件循环后台执行（调用方必在 App 线程，_exec 保证）。
        MarkdownStream 内部合并过密 chunk、后台串行解析未解析行，长文档不再每 chunk 整篇重建块；
        失败仅记录（卡片可能已被清屏移除等竞态）。"""

        async def _guarded() -> None:
            try:
                await coro
            except Exception:
                self.log.warning(f"{what} failed", exc_info=True)

        asyncio.get_running_loop().create_task(_guarded())

    def _stop_stream(self) -> None:
        """停掉当前流卡片的 MarkdownStream 后台任务并复位句柄（回合结束 / 清屏），防后台任务泄漏"""
        self._active_stream = None
        if self._markdown_stream is not None:
            stream, self._markdown_stream = self._markdown_stream, None
            self._run_stream_task(stream.stop(), "markdown stream stop")

    def _stream_update(self, chunk: str) -> None:
        """流式增量：chunk 为本次新增片段（调用方勿传累计全量，会重复追加）"""
        if not chunk:
            return
        md = self._active_stream
        if md is None:  # 首 chunk：建卡并挂增量渲染流
            md = self._add_markdown_card("")
            self._active_stream = md
            self._markdown_stream = Markdown.get_stream(md)
        self._run_stream_task(self._markdown_stream.write(chunk), "markdown stream update")

    # ---------- 用户输入回合 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self._prompt().text = ""
        if not query:
            return
        self._chat().anchor()  # 新回合开始：滚回底部并重新钉底（用户可能正浏览历史）
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/sessions":
            self._open_sessions()
        elif cmd == "/skills":
            self._open_skills()
        elif cmd == "/provider":
            self._open_providers()
        elif cmd == "/model":
            self._open_models()
        elif cmd == "/effort":
            self._open_effort()
        elif self._busy:
            # 回合进行中拒绝 /new 与普通消息：/new 若清空会话指针，本轮后续 add_message
            # 会把回话写进新建的会话（见 session.py：current_session 为空时自动 new_session）
            render_background_notification("Previous turn is still running, please wait…", title="⏳ Busy")
        elif cmd == "/new":
            # 延迟创建：仅丢弃当前会话指针，下一条用户消息到达时由 add_message() 自动建新会话，避免空会话
            if self._manager is not None:
                self._manager.current_session = ""
            self._clear_cards()
        else:
            self._send_user_query(query)

    def _send_user_query(self, query: str) -> None:
        """把一条文本作为用户消息发出（输入条回车与 /skills 选中技能共用同一回合路径）"""
        self._prompt().text = ""  # 输入已消费即清空（技能填入场景：填入后立即发出，同手打回车）
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
                self._handle(query)
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
        self.query_one("#perm-list", OptionList).styles.display = "none"  # 兜底收起（回答流程内已隐藏）
        self._set_status_text("")
        prompt = self._prompt()
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = False
        prompt.focus()

    # ---------- 会话 ----------

    def _open_sessions(self) -> None:
        if self._manager is None:
            render_background_notification("SessionManager not connected — /sessions unavailable", title="⚠️ Sessions")
            return
        sessions = self._manager.load_session_list()
        if not sessions:
            render_sessions()  # 空列表提示卡片
            return
        self.push_screen(SessionPickerScreen(self._manager), callback=self._on_session_picked)

    def _open_skills(self) -> None:
        """/skills：取 core.skill 扫描到的技能（名 → {name, description, content}）在
        SkillsScreen（OptionList 弹窗，同 /provider /model 样式）展示；无技能时给出提示卡。"""
        rows = list(_skills.SKILL_REGISTRY.values())
        if not rows:
            render_background_notification("No skills found: add SKILL.md manifests under .harness/skills",
                                           title="⚠️ Skills")
            return
        self.push_screen(SkillsScreen(rows), callback=self._on_skill_picked)

    def _on_skill_picked(self, skill_name: Optional[str]) -> None:
        """SkillsScreen 关闭回调：选中技能 → 输入条填入 "Invoke skill '<name>'" 并作为用户消息
        发出（弹窗已关）；None = Esc 取消不动；busy 时拒绝发送（防双回合并发）。"""
        if not skill_name:
            return
        if self._busy:
            render_background_notification("Previous turn is still running, please wait…", title="⏳ Busy")
            return
        query = f"Invoke skill '{skill_name}'"
        self._prompt().text = query  # 填入输入条；随后随发送清空（效果同手打回车）
        self._send_user_query(query)

    def _open_providers(self) -> None:
        """/provider：取 provider 配置列表并在 ProviderScreen（OptionList 弹窗）展示。
        core.model 懒加载：演示/冒烟环境未装 openai 等依赖时给出提示卡而非崩溃。"""
        try:
            from core.client import get_provider_list
            rows = get_provider_list()
        except Exception as exc:
            render_background_notification(f"{exc}", title="⚠️ Provider List")
            return
        # 关闭后即时刷新页脚模型状态（Delete 可能删除了当前默认提供商；2s 轮询兜底）
        self.push_screen(ProviderScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_models(self) -> None:
        """/model：取可用模型列表并在 ModelPickerScreen（OptionList 弹窗）展示，
        Enter 经 shared_model_client().set_model_client 切换当前模型（thinking_level 不参与）。
        core.model 懒加载：演示/冒烟环境未装 openai 等依赖时给出提示卡而非崩溃。"""
        try:
            from core.client import get_model_list
            rows = get_model_list()
        except Exception as exc:
            render_background_notification(f"{exc}", title="⚠️ Model List")
            return
        if not rows:
            render_background_notification("No models available: configure a provider via /provider first",
                                           title="⚠️ Model List")
            return
        # 关闭弹窗后即时刷新页脚右端模型状态（set_model_client 已切换；2s 轮询兜底）
        self.push_screen(ModelPickerScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_effort(self) -> None:
        """/effort：Tabs 弹窗（EffortScreen，五档 minimal→max）选思考深度，Enter 经
        shared_model_client().set_thinking_level 生效。当前档位取自 client.current_thinking_level
        （页脚 '(provider) model * level' 的 level 即它）；core.model 懒加载：演示/冒烟环境未装
        openai 等依赖时给出提示卡而非崩溃。"""
        try:
            from core.client import shared_model_client
            client = shared_model_client()
            current = getattr(client, "current_thinking_level", "") or ""
        except Exception as exc:
            render_background_notification(f"{exc}", title="⚠️ Effort List")
            return
        # 关闭后即时刷新页脚模型状态（档位后缀 * level；2s 轮询兜底）
        self.push_screen(EffortScreen(current), callback=lambda _: self._refresh_footer())

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

    # ---------- 权限确认（内联卡片 + 停靠区 yes/no 列表，避免跨线程推屏挂载竞态；
    # ponytail: 同一时刻仅一个权限请求（agent 单线程），并发请求需改请求队列） ----------

    def _begin_permission(self, message: str) -> None:
        """权限确认：渲染确认卡 + 停靠区弹出 yes/no 列表并聚焦，Enter 确认高亮项 / Esc 拒绝。
        输入条保持禁用（回答只走列表，不键入）。"""
        self._add_card("perm", _markup(f"[bold #fef3c7]{escape(message)}[/bold #fef3c7]"))
        self._perm_pending = True
        self._prompt().disabled = True  # 等待作答期间输入条禁用（回合内本就禁用，此处兜底）
        perm = self.query_one("#perm-list", OptionList)
        perm.styles.display = "block"  # 先显示再落高亮（watch_highlighted 会滚动，隐藏态无内容区）
        perm.highlighted = 1  # 高亮默认落在 No（拒绝），与旧版 [y/N] 默认一致
        perm.focus()
        self._set_status_text("Confirm permission: ↑/↓ Yes/No, Enter confirm, Esc reject.", spin=True)

    def _answer_permission(self, value: str) -> None:
        """落地权限回答：写回渲染桥等待线程并放行（ask_permission 返回该值），收起列表；
        回合继续，输入条由 _set_idle 在回合结束时统一恢复。"""
        if not self._perm_pending:
            return
        self._perm_holder["value"] = value
        self._perm_done.set()
        self._perm_pending = False
        self.query_one("#perm-list", OptionList).styles.display = "none"
        self._set_status_text("Working…", spin=True)

    def action_deny_permission(self) -> None:
        """Esc 键位：权限确认优先（挂起请求 → 拒绝）；无挂起请求且回合进行中 → 中断 agent 并 notify；
        空闲时空操作（正常回合内键入 Esc 不受影响）。中断后回合由 _turn_worker 收尾并复位忙碌态。"""
        if self._perm_pending:
            self._answer_permission("no")
        elif self._busy and self._interrupt:
            self._set_status_text("Interrupting…", spin=True)
            self._interrupt()
            self.notify("User interrupted")

    def _clear_cards(self) -> None:
        self._chat().remove_children()
        self._stop_stream()
        self._chat().anchor()  # 空列表无滚动位置；重新钉底供后续内容/历史回放跟随


def _default_agent_turn() -> tuple[Callable[[str], None], Callable[[], None]]:
    """无 handle_query 时的默认回合：接 core.loop_with_interrupt 的真实 agent，
    返回 (回合回调, 中断回调)。延迟导入：--smoke 与离线 UI 演示不必拉起 openai / mcp 依赖。"""
    from core.loop_with_interrupt import make_agent_turn, start_agent_runtime

    runtime = start_agent_runtime()
    return make_agent_turn(runtime), runtime.interrupt


def run(handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Any = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
        on_interrupt: Optional[Callable[[], None]] = None) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    不传 handle_query 时使用 core.loop_with_interrupt 的真实 agent 回合（需已配置模型；
    离线 UI 演示见 core/tui/demo.py，由 --smoke 使用），其 on_interrupt 默认接 AgentRuntime.interrupt。
    传入 session_manager（如 core.session 的 SESSION_MANAGER）后 /new /sessions 可用。
    Esc：权限确认优先；确认结束后若回合仍在进行，则中断。
    运行期间忽略 SIGINT：Ctrl+C 不关闭应用（退出用 /exit 或 ctrl+q），退出后恢复原处理器。
    """
    app = ChatApp(handle_query=handle_query, session_manager=session_manager, banner=banner,
                  on_interrupt=on_interrupt)
    _render._APP = app  # run 期间渲染 API（core.tui.render）经此全局桥接进事件循环
    old_sigint = None
    try:
        if threading.current_thread() is threading.main_thread():
            # Ctrl+C 在部分终端（Windows 控制台 / mintty 等）以 SIGINT 送达而非按键事件；
            # Textual 全屏运行期终端处于 raw 模式，按键通道由 BINDINGS 处理（ctrl+c 已置空操作）
            old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        app.run()
    finally:
        if old_sigint is not None:
            signal.signal(signal.SIGINT, old_sigint)
        _render._APP = None


# ======================================================================
# 启动入口：`python -m core.tui.ui_textual`（不传 handle_query → 真实 agent，见 _default_agent_turn）；
# `--smoke` 自检见 core/tui/smoke.py（拆出以免近 800 行自检代码淹没本模块）
# ======================================================================

if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        from core.tui import smoke

        smoke.main()
    else:
        run()
