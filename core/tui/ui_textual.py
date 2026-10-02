"""Textual 全屏对话界面：ChatApp（欢迎标题 / 聊天画板 / 右栏信息区 / 底部输入区）+ run() 启动入口。

周边模块：theme.py 主题常量、app.css 样式表、widgets.py 输入框与补全、screens/ 各弹窗、
panels.py 左栏分区部件、cards.py 折叠卡片、info_panel.py 右栏信息面板、
render.py 线程安全渲染 API（任意线程可调，驱动本模块内部方法）、demo.py 离线演示 Agent、
smoke/ 冒烟自检（`python -m core.tui.ui_textual --smoke`）。

线程模型：
    App 事件循环跑主线程；每个用户回合在独立后台线程里调用 handle_query。
    渲染函数（core.tui.render）可从任意线程调用（App 线程内直接执行，其它线程经 call_from_thread 桥接）。
    ask_permission 只能在非 App 线程调用（阻塞等待用户从停靠区 yes/no 列表作答）。
    ask_clarify 同理，阻塞等待用户从停靠区选项列表（含 Other 输入）作答。
    Esc：权限确认挂起时先拒绝；否则中断进行中的回合/压缩（on_interrupt，默认 AgentRuntime.interrupt）。

接入自有 Agent：
    from core.tui.ui_textual import run
    run(handle_query=agent_turn, session_manager=SESSION_MANAGER, runtime=runtime)
    # agent_turn(query) 在后台线程执行，须阻塞至回合结束（UI 靠它判断忙碌态并上屏错误）；
    # runtime（core.loop_with_interrupt.start_agent_runtime() 的返回值）供 /compact 用。
"""

from __future__ import annotations

import asyncio
import signal
import threading
from concurrent.futures import Future
from typing import Any, Callable, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option
from textual.widgets.markdown import MarkdownStream

import core.tui.render as _render  # run() 期间把 ChatApp 实例挂到渲染桥接的 _APP 全局（见 run()）
from core.runtime_context import AgentInterrupted  # Esc 中断（另见 asyncio.CancelledError，属 BaseException）
from core.session.session import SessionManager
from core.skill import skills as _skills  # /skills 弹窗数据源（模块导入时 scan_skills() 扫描 .harness/skills）
from core.tui.cards import _CappedCardBody
from core.tui.info_panel import _InfoPanel
from core.tui.panels import _ChatBoard, _ChatDock, _Welcome
from core.tui.render import (
    render_background_notification,
    render_sessions,
    render_session_history,
    render_user_input,
)
from core.tui.screens import (
    EffortScreen,
    ForkScreen,
    LoginScreen,
    MCPServersScreen,
    ModelPickerScreen,
    ProviderScreen,
    SessionPickerScreen,
    SkillsScreen,
)
from core.tui.theme import (
    DEFAULT_SUBTITLE,
    DEFAULT_TITLE,
    _PLACEHOLDER,
    _SPINNER_FRAMES,
)
from core.tui.utils import current_git_branch, current_model_state, working_directory
from core.tui.widgets import ClarifyConfirmed, _ClarifyList, _CommandInput


class _ChatScreen(Screen):
    """主屏（左聊板 / 右栏 / 底部停靠区所在屏）：焦点始终留在输入栏。

    焦点变更都汇经 Screen.set_focus（Tab 切焦、点击可聚焦部件、弹窗关闭后的焦点还原），
    在此统一改道：目标不是 #prompt 时交回输入栏——输入信息无需先点输入框。
    例外：权限确认 / clarify 期间对应列表或输入条持焦作答、输入栏禁用（回合执行中）时目标按原样落下
    （回合结束 _set_idle 收回焦点）。
    """

    def set_focus(self, widget, scroll_visible=True, from_app_focus=False) -> None:
        if widget is not None and self.app.screen is self and not self.app._perm_pending \
                and not self.app._clarify_pending:
            try:
                prompt = self.query_one("#prompt")
            except NoMatches:  # 挂载早期 / 卸载期无输入栏：按原样落下
                pass
            else:
                if widget is not prompt and not prompt.disabled:
                    widget = prompt
        super().set_focus(widget, scroll_visible=scroll_visible, from_app_focus=from_app_focus)


class ChatApp(App):
    """Nano Harness 对话主界面：居中欢迎标题 + 卡片式聊天记录 + 状态行 + 输入框。

    handle_query(query): 每轮用户消息回调，在后台线程执行（可调用本模块渲染函数）；
                         不传则用真实 agent 回合（见 _default_agent_turn）。
    on_interrupt(): 中断当前回合/压缩的回调（Esc，权限确认优先）；未传时取 runtime.interrupt。
    session_manager: 可选，提供 new_session / delete_session / load_session_list /
                     load_session / add_message / current_session 的对象（如 core.session 的 SESSION_MANAGER）。
    runtime: 可选，AgentRuntime（start_agent_runtime() 的返回值），供 /compact 压缩当前会话。
    """

    CSS_PATH = "app.css"  # 同目录样式表，路径相对本模块文件
    TITLE = "Nano Harness"
    SUB_TITLE = "Textual UI"

    # ctrl+c 覆盖 Textual App 默认的 help_quit（系统绑定）：Ctrl+C 不再提示/退出（退出请用
    # /exit 或 ctrl+q）。有选中文本时复制由更内层绑定完成（输入框自身的 ctrl+c -> copy，
    # 或屏层 screen.copy_text 复制鼠标选中文本），两者都无选中才会落到本动作的空操作分支。
    BINDINGS = [("escape", "deny_permission", "Deny/Interrupt"),  # 权限/clarify 确认优先，其次中断进行中的回合
                ("space", "toggle_clarify", "Toggle clarify selection"),  # 多选勾选（仅 clarify 列表聚焦时生效）
                ("ctrl+c", "copy_or_ignore", "Ignore")]

    # / 弹窗指令 → 打开方法名（别名同表）；多数指令忙碌时也可用（/fork 会换会话，例外）
    _OPENERS = {
        "/sessions": "_open_sessions",
        "/fork": "_open_fork",
        "/skills": "_open_skills",
        "/mcp": "_open_mcp",
        "/provider": "_open_providers",
        "/model": "_open_models",
        "/effort": "_open_effort",
        "/login": "_open_login",
        "/logout": "_open_login",
    }

    def get_default_screen(self) -> Screen:
        """默认屏用 _ChatScreen：set_focus 时把焦点始终押回输入栏（见该类 docstring）。"""
        return _ChatScreen(id="_default")

    def action_copy_or_ignore(self) -> None:
        """Ctrl+C 空操作（覆盖基类 help_quit 的提示；复制见类级 BINDINGS 注释），
        不退出、不提示。退出请用 /exit 或 ctrl+q。"""

    def __init__(self, handle_query: Optional[Callable[[str], None]] = None,
                 session_manager: Optional[SessionManager] = None,
                 banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
                 on_interrupt: Optional[Callable[[], None]] = None,
                 runtime: Any = None) -> None:
        # ansi_color=True：ANSI 颜色按终端调色板显示；False/None 会被换成 Textual 内置真彩主题色，
        # 整体背景与终端主题不一致
        super().__init__(ansi_color=True)
        if handle_query is None:
            handle_query, on_interrupt, runtime = _default_agent_turn()
        self._handle = handle_query
        self._interrupt = on_interrupt or (runtime.interrupt if runtime is not None else None)
        self._runtime = runtime
        self._manager = session_manager
        self._banner = banner
        self._busy = False
        self._markdown_stream: Optional[MarkdownStream] = None  # 流卡片增量渲染句柄（后台合并解析）
        self._status_text = ""
        self._status_spin = False  # 当前状态文本前是否轮播加载动画
        self._spin_cursor = 0
        self._status_interval: Any = None  # 动画 interval 句柄（首次出现动画状态时惰性启动）
        self._perm_pending = False
        self._perm_future: Optional[Future[str]] = None
        self._clarify_pending = False
        self._clarify_future: Optional[Future[str]] = None
        self._clarify_multi = False
        self._clarify_chosen: set[int] = set()  # 多选：已勾选选项的下标
        self._clarify_options: list[str] = []  # 原始选项文本（下标与列表前 N 行一一对应）
        self._clarify_other = False  # Other 输入模式（焦点在 #prompt）
        self._panel_pct: Optional[int] = None  # 右栏宽度百分比（Ctrl+←/→ 调整）；None = 未调整（默认 1fr）

    # ---------- 基础部件 ----------

    def compose(self) -> ComposeResult:
        title, subtitle = self._banner
        with Horizontal(id="main"):  # 左右分栏：左 4fr 现有聊天界面，右 1fr 信息栏（比例见 app.css）
            with Vertical(id="left"):
                yield _Welcome(title, subtitle)  # 空聊板居中欢迎标题（有卡片时由 _set_welcome 隐藏）
                yield _ChatBoard(id="chat")
                yield _ChatDock(id="dock")
            with Vertical(id="right"):  # 右栏左缘 »/« 标签切换；折叠时收成 1 列（聊天区吃满其余宽度）
                with Vertical(id="info-tab"):  # 标签列：宽 1，dock 右栏左缘（分栏线旁）垂直居中
                    tab = Static("»", id="info-tab-glyph")
                    tab.tooltip = "Collapse info panel"  # 图标含义：» 收起右栏 / « 展开右栏
                    yield tab
                yield _InfoPanel(id="info-panel")  # 分区卡片与轮询同步见 core/tui/info_panel.py

    def on_mount(self) -> None:
        self._prompt().focus()
        self._chat().anchor()  # 钉底：溢出时新内容由 compositor 布局自动保持贴底；用户上滚解除、滚回底部自动恢复
        self._set_welcome(True)  # 启动即空聊板：隐藏空白画板，居中显示欢迎标题
        self._refresh_footer()
        # 首帧布局完成后补刷：on_mount 时 footer 宽度还是 0，模型段会被当放不下而丢弃，
        # 等首个 2s 轮询才出现 → 右下角模型信息延迟过大
        self.call_after_refresh(self._refresh_footer)
        self.set_interval(2.0, self._refresh_footer)  # 轮询 cwd/git（用户可能另开终端切目录/分支）

    def on_resize(self, event: events.Resize) -> None:
        """终端尺寸变化时重算页脚右对齐（2s 轮询外保持对齐实时）"""
        self._refresh_footer()

    def on_click(self, event: events.Click) -> None:
        """可展开卡片正文（-expandable 标记）点击：截断 ↔ 完整内容；
        右栏左缘 »/« 标签点击：右栏整体折叠/展开（折叠时聊天区吃满其余宽度）。
        右栏分区标题与条目行的点击由 _InfoPanel 就地处理（已阻止冒泡）。"""
        target = event.widget
        if target.has_class("-expandable"):  # 截断卡片正文
            self._toggle_expand(target)
            return
        if target.id != "info-tab-glyph":
            return
        right = self.query_one("#right", Vertical)
        collapsed = not right.has_class("-collapsed")
        right.set_class(collapsed, "-collapsed")
        if self._panel_pct is not None:  # 调整过宽度：内联宽度优先于 CSS 折叠规则，折叠/展开须同步设宽
            right.styles.width = 1 if collapsed else f"{self._panel_pct}%"
        self.call_after_refresh(self._refresh_footer)  # 左栏宽度随右栏变化，页脚右对齐重算
        target.update("«" if collapsed else "»")  # 图标随状态换向：指向点击后右栏的移动方向
        target.tooltip = "Expand info panel" if collapsed else "Collapse info panel"

    # ---------- 右栏调宽（Ctrl+←/→，绑定见 _CommandInput.BINDINGS） ----------

    def action_narrow_info_panel(self) -> None:
        """Ctrl+→：右栏收窄一档（默认 20%，每档 10%，夹在 20%–40%）"""
        self._resize_info_panel(-10)

    def action_widen_info_panel(self) -> None:
        """Ctrl+←：右栏加宽一档（默认 20%，每档 10%，夹在 20%–40%）"""
        self._resize_info_panel(10)

    def _resize_info_panel(self, delta: int) -> None:
        """按档位设右栏宽度（百分比随终端宽度缩放）；折叠态忽略（先点 »/« 展开）。"""
        right = self.query_one("#right", Vertical)
        if right.has_class("-collapsed"):
            return
        self._panel_pct = max(20, min(40, (self._panel_pct or 20) + delta))
        right.styles.width = f"{self._panel_pct}%"
        self.call_after_refresh(self._refresh_footer)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """停靠区列表的选中（冒泡统一收口）：权限列表直接作答（yes/no 选项 id）、clarify 列表
        走 _select_clarify；/ 指令把选中项 id（= 指令串）填进输入；@ 文件把选中路径补全进输入
        （均不发送）。弹窗内各自的 OptionList 已在弹窗内 stop，冒泡至此的按 id 过滤。"""
        oid = event.option_list.id
        if oid == "perm-list":  # 选项 id 固定 yes/no（见 panels._ChatDock.compose）
            event.stop()
            self._answer_permission(event.option_id or "no")
            return
        if oid == "clarify-list":  # 点击：单选直接作答、多选切换勾选、Other 走输入模式（见 _select_clarify）
            event.stop()
            self._select_clarify(event.option_index)
            return
        prompt = self._prompt()
        if oid == "cmd-suggest":
            event.stop()
            cmd = event.option_id  # 行含别名括注时 id 仍是规范指令串
            prompt.text = cmd
            prompt.cursor_location = (0, len(cmd))
            prompt.focus()
        elif oid == "file-suggest":
            event.stop()
            if prompt.apply_file_candidate(event.option_index):
                prompt.focus()

    def _chat(self) -> _ChatBoard:
        return self.query_one("#chat", _ChatBoard)

    def _status(self) -> Static:
        return self.query_one("#status", Static)

    def _prompt(self) -> _CommandInput:
        return self.query_one("#prompt", _CommandInput)

    # ---------- 页脚（cwd / git 分支 / 当前模型） ----------

    def _current_model_label(self) -> str:
        """当前模型信息串 '(provider) model * level'；未配置返回空串（页脚只显 cwd），
        无 level 时省略 ' * level' 后缀。"""
        provider, model, level = current_model_state()
        if not provider or not model:
            return ""
        return f"({provider}) {model}" + (f" * {level}" if level else "")

    def _refresh_footer(self) -> None:
        """刷新最底行：左 = 当前工作目录 (git 分支)，右 = 模型状态（暗灰右对齐；
        空间不足时只保留 cwd）。"""
        cwd = working_directory()
        branch = current_git_branch()
        label = f"{cwd} ({branch})" if branch else cwd
        footer = self.query_one("#footer", Static)
        text = Text(label, no_wrap=True, style="#64748b")
        model_label = self._current_model_label()
        if model_label:
            model = Text(model_label, no_wrap=True, style="#64748b")
            gap = footer.content_region.width - text.cell_len - model.cell_len
            if gap >= 0:
                text.append(" " * gap)
                text.append_text(model)
        footer.update(text)

    # ---------- 卡片 ----------

    def _add_card(self, kind: str, body: Any, cap: Optional[int] = None,
                  reserved: int = 0) -> Static:
        """追加一张卡片，返回 body Static。

        cap: 非 None 时正文按渲染后的视觉行折叠（超过 cap 行只显示前 cap 行 + 提示行，
        点击在截断与完整内容间切换）；reserved 为固定头行数（不计入 cap）。"""
        self._set_welcome(False)  # 首卡上屏：欢迎标题让位给聊板
        body_w = (_CappedCardBody(body, cap, reserved, classes="card-body", markup=False)
                  if cap is not None else Static(body, classes="card-body", markup=False))
        self._chat().mount(Vertical(body_w, classes=f"card {kind}"))
        return body_w

    def _toggle_expand(self, body_w: Static) -> None:
        """可展开卡片正文点击：截断 ↔ 完整内容（_CappedCardBody 实现）"""
        body_w.toggle_expand()

    def _add_markdown_card(self, content: str) -> Markdown:
        """追加一张 Assistant Markdown 卡片（链接可点击 → App.open_url 交给系统浏览器）"""
        self._set_welcome(False)  # 首卡上屏：欢迎标题让位给聊板
        md = Markdown(content, open_links=True)  # 块级内容 mount 后异步解析
        self._chat().mount(Vertical(md, classes="card assistant"))
        return md

    # ---------- 状态文本（render_thinking_status / render_tool_calling_status 共用） ----------

    def _set_status_text(self, text: str, spin: bool = False) -> None:
        """设置状态行文本；spin=True 时由 interval 在文本前轮播加载动画帧（帧不存入 _status_text）"""
        self._status_text = text
        self._status_spin = spin
        if spin:
            self._spin_cursor = 0
            self._tick_status()  # 立即渲染首帧（此后由 interval 推进）
            if self._status_interval is None:
                self._status_interval = self.set_interval(0.1, self._tick_status)
        else:
            self._status().update(text)
            if self._status_interval is not None:
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
        """把流协程挂到当前事件循环后台执行（调用方必在 App 线程，render 的 _exec 保证）：
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
        if self._markdown_stream is not None:
            stream, self._markdown_stream = self._markdown_stream, None
            self._run_stream_task(stream.stop(), "markdown stream stop")

    def _stream_update(self, chunk: str) -> None:
        """流式增量：chunk 为本次新增片段（调用方勿传累计全量，会重复追加）"""
        if not chunk:
            return
        if self._markdown_stream is None:  # 首 chunk：建卡并挂增量渲染流
            self._markdown_stream = Markdown.get_stream(self._add_markdown_card(""))
        self._run_stream_task(self._markdown_stream.write(chunk), "markdown stream update")

    # ---------- 用户输入回合 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._clarify_pending and self._clarify_other:  # clarify 的 Other 输入：不当作新回合
            self._prompt().text = ""
            text = event.value.strip()
            if text:
                self._answer_clarify(self._clarify_answer(text) if self._clarify_multi else text)
            else:
                self._leave_clarify_other()  # 空提交：回到选项列表继续选择
            return
        query = event.value.strip()
        self._prompt().text = ""
        if not query:
            return
        self._chat().anchor()  # 新回合开始：滚回底部并重新钉底（用户可能正浏览历史）
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif opener := self._OPENERS.get(cmd):
            getattr(self, opener)()
        elif self._busy:
            # 回合进行中拒绝 /compact /new 与普通消息：/new 若清空会话指针，本轮后续 add_message
            # 会把回话写进新建的会话（见 session.py：current_session 为空时自动 new_session）
            self._reject_busy()
        elif cmd == "/compact":
            self._run_compact()
        elif cmd == "/new":
            # 延迟创建：仅丢弃当前会话指针，下一条用户消息到达时由 add_message() 自动建新会话，避免空会话
            if self._manager is not None:
                self._manager.current_session = ""
            self._clear_cards()
        else:
            self._send_user_query(query)

    def _reject_busy(self) -> None:
        """回合进行中：新回合（含技能发送）被拒，提示后返回。"""
        render_background_notification("Previous turn is still running, please wait…", title="⏳ Busy")

    def _send_user_query(self, query: str) -> None:
        """把一条文本作为用户消息发出（输入条回车与 /skills 选中技能共用同一回合路径）"""
        self._prompt().text = ""  # 输入已消费即清空（技能填入场景：填入后立即发出，同手打回车）
        self._busy = True
        self._prompt().disabled = True
        self._set_status_text("Working…", spin=True)
        render_user_input(query)
        self._spawn_worker(lambda: self._turn_worker(query), "agent-turn", "⚠️ Agent Error")

    def _turn_worker(self, query: str) -> None:
        if self._manager is not None:
            try:
                self._manager.add_message({"role": "user", "content": query})
            except Exception as exc:
                render_background_notification(f"保存会话消息失败：{exc}", title="⚠️ Session Error")
        self._handle(query)

    def _spawn_worker(self, work: Callable[[], None], name: str, title: str) -> threading.Thread:
        """后台线程跑 work：线程内异常上屏，收尾回到 App 线程复位忙碌态（App 已退出则忽略）

        调用方不等线程结束（收尾走 _set_idle）；返回句柄便于冒烟 / 测试 join。"""

        def _run() -> None:
            try:
                work()
            except Exception as exc:
                render_background_notification(f"{type(exc).__name__}: {exc}", title=title)
            finally:
                try:
                    self.call_from_thread(self._set_idle)
                except RuntimeError:
                    pass  # App 已退出

        thread = threading.Thread(target=_run, daemon=True, name=name)
        thread.start()
        return thread

    def _set_idle(self) -> None:
        """回合 / 压缩结束：复位忙碌态、收起权限/clarify 列表、恢复输入条并收回焦点"""
        self._busy = False
        self._perm_pending = False
        self._perm_future = None
        self._clarify_pending = False
        self._clarify_other = False
        self._clarify_future = None
        self.query_one("#perm-list", OptionList).styles.display = "none"  # 兜底收起（回答流程内已隐藏）
        self.query_one("#clarify-list", _ClarifyList).styles.display = "none"  # 同上
        self._set_status_text("")
        prompt = self._prompt()
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = False
        prompt.focus()

    # ---------- 手动压缩（/compact） ----------

    def _run_compact(self) -> None:
        """后台线程持 AGENT_LOCK 调 AgentRuntime.compact 压缩当前会话（与 agent 回合 / cron 串行）；
        回合进行中由 on_input_submitted 的 busy 分支拒绝，到不了这里。"""
        if self._runtime is None:
            render_background_notification("AgentRuntime not connected — /compact unavailable", title="⚠️ Compact")
            return
        if self._manager and not self._manager.current_session:
            render_background_notification("Current session is empty — /compact unavailable", title="⚠️ Compact")
            return
        self._busy = True  # 压缩期间拒绝新回合（worker 收尾走 _set_idle 复位）
        self._prompt().disabled = True
        self._set_status_text("Compacting…", spin=True)
        self._spawn_worker(lambda: self._compact_worker(self._runtime), "agent-compact", "⚠️ Compact Error")

    def _compact_worker(self, runtime: Any) -> None:
        """压缩线程主体：AGENT_LOCK 在协程外取（协程内取会阻塞事件循环，与持锁等待该循环的
        cron 线程死等），submit 阻塞至压缩完成。期间 Esc（action_deny_permission → interrupt）
        可中断：中断则不落会话，历史保持原样。"""
        from core.loop_with_interrupt import AGENT_LOCK  # 延迟导入：离线/冒烟环境未装 openai
        with AGENT_LOCK:
            try:
                runtime.submit(runtime.compact())
            except AgentInterrupted:  # submit 把 ctx/task 两路取消统一成 AgentInterrupted
                render_background_notification("Compaction interrupted — history unchanged.", title="⏹ Compact")
                return
        self.call_from_thread(self._reload_history)
        render_background_notification("Context compacted — history re-rendered.", title="🗜 Compact")

    # ---------- 会话 / 弹窗 ----------

    def _client_call(self, getter: str, title: str) -> Any:
        """懒加载调用 core.client.<getter>()；未装 openai 等依赖（演示/冒烟环境）时提示卡并返回 None"""
        try:
            import core.client as client
            return getattr(client, getter)()
        except Exception as exc:
            render_background_notification(str(exc), title=title)
            return None

    def _open_sessions(self) -> None:
        if self._manager is None:
            render_background_notification("SessionManager not connected — /sessions unavailable", title="⚠️ Sessions")
            return
        sessions = self._manager.load_session_list()
        if not sessions:
            render_sessions()  # 空列表提示卡片
            return
        self.push_screen(SessionPickerScreen(self._manager, on_delete_current=self._clear_cards),
                         callback=self._on_session_picked)

    def _open_fork(self) -> None:
        """/fork：当前会话全部用户消息（load_user_messages）在 ForkScreen 弹窗展示；选中消息 →
        fork_session 从该消息前分叉出新会话并重放历史，消息原文填回输入栏供修改。"""
        if self._manager is None:
            render_background_notification("SessionManager not connected — /fork unavailable", title="⚠️ Fork")
            return
        if self._busy:  # 分叉会换当前会话，进行中的回合必须拒绝（其它 / 弹窗指令忙碌可用，此例不同）
            self._reject_busy()
            return
        messages = self._manager.load_user_messages()
        if not messages:
            render_background_notification("No user messages to fork from", title="⚠️ Fork")
            return
        self.push_screen(ForkScreen(messages),
                         callback=lambda message_id: self._on_fork_picked(message_id, messages))

    def _on_fork_picked(self, message_id: Optional[str], messages: list[Any]) -> None:
        """ForkScreen 关闭回调：选中消息 id → 分叉（新会话不含该消息）并重放新会话历史，
        原文填入输入条；None = Esc 取消不动，分叉失败（id 已不在当前会话）只提示。"""
        if not message_id or self._manager is None:
            return
        forked = self._manager.fork_session(message_id)
        if forked is None:
            render_background_notification("Fork failed: message not found", title="⚠️ Fork")
            return
        if forked.messages:
            self._reload_history()
        else:
            self._clear_cards()  # 分叉到起点：新会话尚无历史，清板不回放
        content = next((m.content for m in messages if m.id == message_id), "")
        prompt = self._prompt()
        prompt.text = content
        prompt.cursor_location = prompt.document.end  # text setter 会把光标归位到开头，置于末尾
        prompt.focus()

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
        """SkillsScreen 关闭回调：选中技能 → 作为用户消息发出 "Invoke skill '<name>'"；
        None = Esc 取消不动；busy 时拒绝发送（防双回合并发）。"""
        if not skill_name:
            return
        if self._busy:
            self._reject_busy()
            return
        query = f"Invoke skill '{skill_name}'"
        self._prompt().text = query  # 填入输入条；随后随发送清空（效果同手打回车）
        self._send_user_query(query)

    def _open_mcp(self) -> None:
        """/mcp：取 core.mcp.mcp_client.get_mcp_server_list()（server 名 → 状态 + 工具名/描述）在
        MCPServersScreen 弹窗展示；servers 为空也照常打开（窗内 Insert 配 server）。
        懒导入 + 异常兜底：建连未就绪 / 依赖缺失时只上提示卡；取数走非阻塞 peek
        （不触发建连，未就绪时直接上提示卡）。"""
        try:
            from core.mcp.mcp_client import get_mcp_server_list
            servers = get_mcp_server_list()
        except Exception as exc:
            render_background_notification(str(exc), title="⚠️ MCP Servers")
            return
        self.push_screen(MCPServersScreen(servers))

    def _open_providers(self) -> None:
        """/provider：provider 配置列表（后端原名 + 配置状态）在 ProviderScreen 弹窗展示。
        关闭后即时刷新页脚模型状态（Delete 可能删除了当前默认提供商；2s 轮询兜底）。"""
        rows = self._client_call("get_provider_list", "⚠️ Provider List")
        if rows is not None:
            self.push_screen(ProviderScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_models(self) -> None:
        """/model：可用模型列表在 ModelPickerScreen 弹窗展示，Enter 经
        shared_model_client().set_model_client 切换当前模型（thinking_level 不参与）。"""
        rows = self._client_call("get_model_list", "⚠️ Model List")
        if rows is None:
            return
        if not rows:
            render_background_notification("No models available: configure a provider via /provider first",
                                           title="⚠️ Model List")
            return
        self.push_screen(ModelPickerScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_effort(self) -> None:
        """/effort：EffortScreen（Tabs 弹窗，五档 minimal→max）选思考深度，Enter 经
        shared_model_client().set_thinking_level 生效；当前档位取自 client.current_thinking_level。"""
        client = self._client_call("shared_model_client", "⚠️ Effort List")
        if client is None:
            return
        current = getattr(client, "current_thinking_level", "") or ""
        # 关闭后即时刷新页脚模型状态（档位后缀 * level；2s 轮询兜底）
        self.push_screen(EffortScreen(current), callback=lambda _: self._refresh_footer())

    def _open_login(self) -> None:
        """/login（别名 /logout）：自定义提供方 / 模型注册（注销）菜单弹窗（LoginScreen，
        四项菜单；选项 1 → RegisterProviderScreen 表单经 core.client.login_provider 落盘）。
        关闭后即时刷新页脚模型状态（2s 轮询兜底）。"""
        self.push_screen(LoginScreen(), callback=lambda _: self._refresh_footer())

    def _reload_history(self, session_id: str = "") -> None:
        """按会话最新消息重放聊板（切换会话 / /compact 压缩完成后共用；省略 id = 当前会话）"""
        if self._manager is None:
            return
        session = self._manager.load_session(session_id)
        if session is not None:
            self._clear_cards()
            render_session_history(session)

    def _on_session_picked(self, result: tuple[Optional[str], bool]) -> None:
        session_id, was_empty = result
        if not session_id:
            if was_empty:
                render_sessions()
            return
        self._reload_history(session_id)

    # ---------- 权限确认（内联卡片 + 停靠区 yes/no 列表，避免跨线程推屏挂载竞态；
    # ponytail: 同一时刻仅一个权限请求（agent 单线程），并发请求需改请求队列） ----------

    def _begin_permission(self, message: str, future: Future[str]) -> None:
        """权限确认：渲染确认卡 + 停靠区弹出 yes/no 列表并聚焦，Enter 确认高亮项 / Esc 拒绝。
        输入条保持禁用（回答只走列表，不键入）；future 由发起线程等待，用户作答时落入结果。"""
        self._perm_future = future
        self._add_card("perm", Text(message, style="bold #fef3c7"))
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
        future, self._perm_future = self._perm_future, None
        self._perm_pending = False
        self.query_one("#perm-list", OptionList).styles.display = "none"
        self._set_status_text("Working…", spin=True)
        if future is not None:
            future.set_result(value)

    def action_deny_permission(self) -> None:
        """Esc 键位：权限确认优先（挂起请求 → 拒绝），其次 clarify（挂起请求 → 取消）；
        无挂起请求且回合进行中 → 中断 agent 并 notify；空闲时空操作。中断后回合由
        _turn_worker 收尾并复位忙碌态。"""
        if self._perm_pending:
            self._answer_permission("no")
        elif self._clarify_pending:
            self._answer_clarify("[User cancelled]")
        elif self._busy and self._interrupt:
            self._set_status_text("Interrupting…", spin=True)
            self._interrupt()
            self.notify("User interrupted")

    # ---------- clarify 询问（clarify 工具：停靠区选项列表 + 末尾 Other 输入；与权限确认
    # 同为阻塞式后台线程等待；ponytail: 同一时刻仅一个澄清请求（agent 工具串行），并发需改请求队列） ----------

    def _begin_clarify(self, questions: list[str], multi_select: bool, future: Future[str]) -> None:
        """澄清询问：停靠区弹出选项列表（末尾 Other 用于自由输入）并聚焦；
        单选 Enter/点击直接作答，多选 Space 勾选、Enter 提交；future 由发起线程等待。"""
        self._clarify_future = future
        self._clarify_pending = True
        self._clarify_multi = multi_select
        self._clarify_chosen = set()
        self._clarify_options = list(questions)
        self._clarify_other = False
        ol = self.query_one("#clarify-list", _ClarifyList)
        ol.set_options(self._clarify_rows())
        ol.styles.display = "block"  # 先显示再落高亮（watch_highlighted 会滚动，隐藏态无内容区）
        ol.highlighted = 0
        ol.focus()
        self._prompt().disabled = True  # 仅 Other 输入模式启用输入条，此处兜底（回合内本就禁用）
        self._set_status_text(self._clarify_hint(), spin=True)

    def _clarify_rows(self):
        """选项行：多选加 ◉/◯ 勾选标记；末尾 Other（id=other，其余 id=数字下标）。
        prompt 用 Text：问题文本来自模型，不得按 markup 解析（OptionList 默认 markup=True）"""
        for index, text in enumerate(self._clarify_options):
            label = (f"{'◉' if index in self._clarify_chosen else '◯'} {text}"
                     if self._clarify_multi else text)
            yield Option(Text(label), id=str(index))
        yield Option(Text("Other (type your own answer)", style="dim"), id="other")

    def _clarify_hint(self) -> str:
        return ("Clarify: ↑/↓ move, Space toggle, Enter confirm, Esc cancel." if self._clarify_multi
                else "Clarify: ↑/↓ move, Enter select, Esc cancel.")

    def _select_clarify(self, index: int) -> None:
        """列表第 index 项被选中（点击 / Enter 单选 / Space 多选统一入口）：
        末行 Other → 输入模式，多选 → 切换勾选，单选 → 直接作答。"""
        if not self._clarify_pending or self._clarify_other:
            return
        if index >= len(self._clarify_options):  # 末行固定为 Other（见 _clarify_rows）
            self._enter_clarify_other()
        elif self._clarify_multi:
            self._toggle_clarify(index)
        else:
            self._answer_clarify(self._clarify_options[index])

    def _toggle_clarify(self, index: int) -> None:
        """多选：切换第 index 项勾选并刷新行首 ◉/◯"""
        if index in self._clarify_chosen:
            self._clarify_chosen.discard(index)
        else:
            self._clarify_chosen.add(index)
        ol = self.query_one("#clarify-list", _ClarifyList)
        label = f"{'◉' if index in self._clarify_chosen else '◯'} {self._clarify_options[index]}"
        ol.replace_option_prompt_at_index(index, Text(label))

    def action_toggle_clarify(self) -> None:
        """Space（App 绑定）：多选时切换高亮项勾选；单选 / Other 输入模式空操作"""
        if not (self._clarify_pending and self._clarify_multi and not self._clarify_other):
            return
        ol = self.query_one("#clarify-list", _ClarifyList)
        if ol.has_focus and ol.highlighted is not None:
            self._select_clarify(ol.highlighted)

    def on_clarify_confirmed(self, event: ClarifyConfirmed) -> None:
        """Enter（_ClarifyList 确认消息）：多选提交勾选；单选选中高亮项（Other → 输入模式）"""
        if not self._clarify_pending or self._clarify_other:
            return
        ol = self.query_one("#clarify-list", _ClarifyList)
        if self._clarify_multi:
            self._answer_clarify(self._clarify_answer())
        elif ol.highlighted is not None:
            self._select_clarify(ol.highlighted)

    def _clarify_answer(self, other: str = "") -> str:
        """多选答案：勾选项逐行（Other 文本追加在末行）；空选择给占位文本"""
        chosen = [self._clarify_options[i] for i in sorted(self._clarify_chosen)]
        if other:
            chosen.append(other)
        return "\n".join(f"- {text}" for text in chosen) if chosen else "[User made no selection]"

    def _enter_clarify_other(self) -> None:
        """Other：启用输入条并以提示语占位，等待用户键入自定义回答"""
        self._clarify_other = True
        prompt = self._prompt()
        prompt.text = ""
        prompt.placeholder = "Type your own answer…"
        prompt.disabled = False
        prompt.focus()
        self._set_status_text("Clarify: type your answer, Enter confirm, Esc cancel.", spin=False)

    def _leave_clarify_other(self) -> None:
        """Other 空提交：回到选项列表继续选择"""
        self._clarify_other = False
        self._reset_prompt()
        self.query_one("#clarify-list", _ClarifyList).focus()
        self._set_status_text(self._clarify_hint(), spin=True)

    def _reset_prompt(self) -> None:
        """输入条回到默认占位禁用态（clarify 作答 / Other 空提交共用；回合结束 _set_idle 再启用）"""
        prompt = self._prompt()
        prompt.text = ""
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = True

    def _answer_clarify(self, value: str) -> None:
        """落地澄清回答：写回渲染桥等待线程并收起列表；输入条恢复禁用（回合结束由 _set_idle 统一恢复）"""
        if not self._clarify_pending:
            return
        future, self._clarify_future = self._clarify_future, None
        self._clarify_pending = False
        self._clarify_other = False
        self.query_one("#clarify-list", _ClarifyList).styles.display = "none"
        self._reset_prompt()
        self._set_status_text("Working…", spin=True)
        if future is not None:
            future.set_result(value)

    def _set_welcome(self, show: bool) -> None:
        """切换空聊板欢迎标题：-welcome 类挂在 #left 上，欢迎与聊板互斥显隐（规则见 app.css）"""
        self.query_one("#left", Vertical).set_class(show, "-welcome")

    def _clear_cards(self) -> None:
        self._stop_stream()  # 先停流再移除卡片，避免残留写入已移除的卡片
        self._chat().remove_children()
        self._chat().anchor()  # 空列表无滚动位置；重新钉底供后续内容/历史回放跟随
        self._set_welcome(True)  # 空聊板 → 重新显示居中欢迎标题（/new、空会话共用）


def _default_agent_turn() -> tuple[Callable[[str], None], Callable[[], None], Any]:
    """无 handle_query 时的默认回合：接 core.loop_with_interrupt 的真实 agent，
    返回 (回合回调, 中断回调, runtime)。延迟导入：--smoke 与离线 UI 演示不必拉起 openai / mcp 依赖。"""
    from core.loop_with_interrupt import make_agent_turn, start_agent_runtime

    runtime = start_agent_runtime()
    return make_agent_turn(runtime), runtime.interrupt, runtime


def run(handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Any = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
        on_interrupt: Optional[Callable[[], None]] = None,
        runtime: Any = None) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    不传 handle_query 时使用 core.loop_with_interrupt 的真实 agent 回合（需已配置模型；
    离线 UI 演示见 core/tui/demo.py，由 --smoke 使用），其 on_interrupt 默认接 AgentRuntime.interrupt。
    传入 session_manager（如 core.session 的 SESSION_MANAGER）后 /new /sessions 可用；
    传入 runtime（start_agent_runtime() 的返回值）后 /compact 可用。
    Esc：权限确认优先；确认结束后若回合仍在进行，则中断。
    运行期间忽略 SIGINT：Ctrl+C 不关闭应用（退出用 /exit 或 ctrl+q），退出后恢复原处理器。
    """
    app = ChatApp(handle_query=handle_query, session_manager=session_manager, banner=banner,
                  on_interrupt=on_interrupt, runtime=runtime)
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
# `--smoke` 自检见 core/tui/smoke/
# ======================================================================

if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        from core.tui import smoke

        smoke.main()
    else:
        run()
