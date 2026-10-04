"""Textual 全屏对话界面：ChatApp 装配 + run() 启动入口（`python -m core.tui.ui_textual [--smoke]`）。

职责分片：surface.py（卡片 / 流式 / 状态行）、footer.py（页脚）、commands.py（弹窗指令 / 回合 /
压缩）、interactions.py（权限确认 / clarify）；弹窗 screens/、右栏 info_panel.py、渲染桥 render.py。

线程模型：App 事件循环跑主线程；每回合在后台线程调 handle_query；render 可从任意线程调用
（App 线程内直接执行，其它线程经 call_from_thread 桥接）；ask_permission / ask_clarify 只能
非 App 线程调用，阻塞等待停靠区作答。接入自有 Agent 见 run()。
"""

from __future__ import annotations

import signal
import threading
from typing import Any, Callable, Optional

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

import core.tui.render as _render  # run() 把 ChatApp 挂到渲染桥接的 _APP 全局
from core.context.session import SessionManager
from core.tui.commands import _CommandFlow
from core.tui.footer import _FooterBar
from core.tui.info_panel import _InfoPanel
from core.tui.interactions import _DockInteractions
from core.tui.panels import _ChatBoard, _ChatDock, _Welcome
from core.tui.surface import _RenderSurface
from core.tui.theme import DEFAULT_SUBTITLE, DEFAULT_TITLE
from core.tui.widgets import _CommandInput


class _ChatScreen(Screen):
    """主屏：焦点变更一律改道输入栏（权限/澄清作答、输入栏禁用时除外）。"""

    def set_focus(self, widget, scroll_visible=True, from_app_focus=False) -> None:
        if widget is not None and self.app.screen is self and not self.app._perm_pending \
                and not self.app._clarify_pending:
            try:
                prompt = self.query_one("#prompt")
            except NoMatches:  # 挂载早期 / 卸载期无输入栏
                pass
            else:
                if widget is not prompt and not prompt.disabled:
                    widget = prompt
        super().set_focus(widget, scroll_visible=scroll_visible, from_app_focus=from_app_focus)


class ChatApp(_RenderSurface, _CommandFlow, _DockInteractions, App):
    """Nano Harness 对话主界面。

    handle_query(query)：每轮消息回调，后台线程执行（不传用真实 agent 回合，见 _default_agent_turn）；
    on_interrupt()：Esc 中断回调（默认 runtime.interrupt）；
    session_manager：提供 new/delete/load_session* / add_message / current_session 的对象；
    runtime：AgentRuntime（/compact 压缩用）。
    """

    CSS_PATH = "app.css"
    TITLE = "Nano Harness"
    SUB_TITLE = "Textual UI"

    # ctrl+c 空操作覆盖 Textual 默认的 help_quit（退出用 /exit 或 ctrl+q；复制由更内层绑定处理）
    BINDINGS = [("escape", "deny_permission", "Deny/Interrupt"),  # 权限/clarify 优先，其次中断回合
                ("space", "toggle_clarify", "Toggle clarify selection"),
                ("ctrl+c", "copy_or_ignore", "Ignore")]

    _OPENERS = {  # / 指令 → 打开方法名（方法见 commands._CommandFlow）
        "/sessions": "_open_sessions", "/fork": "_open_fork", "/skills": "_open_skills",
        "/mcp": "_open_mcp", "/provider": "_open_providers", "/model": "_open_models",
        "/effort": "_open_effort", "/login": "_open_login", "/logout": "_open_login",
    }

    def __init__(self, handle_query: Optional[Callable[[str], None]] = None,
                 session_manager: Optional[SessionManager] = None,
                 banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
                 on_interrupt: Optional[Callable[[], None]] = None,
                 runtime: Any = None) -> None:
        super().__init__(ansi_color=True)  # 按终端调色板显示 ANSI 色，避免与终端主题不一致
        if handle_query is None:
            handle_query, on_interrupt, runtime = _default_agent_turn()
        self._handle = handle_query
        self._interrupt = on_interrupt or (runtime.interrupt if runtime is not None else None)
        self._runtime = runtime
        self._manager = session_manager
        self._banner = banner
        self._busy = False
        self._markdown_stream: Any = None  # 流式卡片增量渲染句柄（surface._RenderSurface）
        self._perm_pending = False
        self._perm_future: Any = None
        self._clarify_pending = False
        self._clarify_future: Any = None
        self._clarify_multi = False
        self._clarify_chosen: set[int] = set()
        self._clarify_options: list[str] = []
        self._clarify_other = False
        self._panel_pct: Optional[int] = None  # 右栏宽度百分比（Ctrl+←/→ 调整）；None = 默认

    def get_default_screen(self) -> Screen:
        return _ChatScreen(id="_default")

    def action_copy_or_ignore(self) -> None:
        """Ctrl+C 空操作（覆盖基类退出提示）。"""

    def compose(self) -> ComposeResult:
        title, subtitle = self._banner
        with Horizontal(id="main"):  # 左 4fr 聊天区 / 右 1fr 信息栏（比例见 app.css）
            with Vertical(id="left"):
                yield _Welcome(title, subtitle)
                yield _ChatBoard(id="chat")
                yield _ChatDock(id="dock")
            with Vertical(id="right"):
                with Vertical(id="info-tab"):  # 分栏线旁的 »/« 折叠开关
                    tab = Static("»", id="info-tab-glyph")
                    tab.tooltip = "Collapse info panel"
                    yield tab
                yield _InfoPanel(id="info-panel")

    def on_mount(self) -> None:
        self._prompt().focus()
        self._chat().anchor()  # 钉底：溢出时保持贴底，用户上滚解除、滚回底部恢复
        self._set_welcome(True)
        self._refresh_footer()

    def on_resize(self, event: events.Resize) -> None:
        self._refresh_footer()  # 页脚右对齐随终端宽度实时重算

    def on_click(self, event: events.Click) -> None:
        """卡片正文点击 = 展开/收起；»/« 点击 = 折叠/展开右栏（分区点击由 _InfoPanel 处理）。"""
        target = event.widget
        if target.has_class("-expandable"):
            self._toggle_expand(target)
            return
        if target.id != "info-tab-glyph":
            return
        right = self.query_one("#right", Vertical)
        collapsed = not right.has_class("-collapsed")
        right.set_class(collapsed, "-collapsed")
        if self._panel_pct is not None:  # 内联宽度优先于 CSS 折叠规则，折叠/展开须同步设宽
            right.styles.width = 1 if collapsed else f"{self._panel_pct}%"
        self.call_after_refresh(self._refresh_footer)  # 左栏宽度随右栏变化
        target.update("«" if collapsed else "»")
        target.tooltip = "Expand info panel" if collapsed else "Collapse info panel"

    # ---------- 右栏调宽（Ctrl+←/→，绑定见 _CommandInput.BINDINGS） ----------

    def action_narrow_info_panel(self) -> None:
        self._resize_info_panel(-10)

    def action_widen_info_panel(self) -> None:
        self._resize_info_panel(10)

    def _resize_info_panel(self, delta: int) -> None:
        """按档位设右栏宽度（默认 20%，每档 10%，夹在 20%–40%）；折叠态忽略。"""
        right = self.query_one("#right", Vertical)
        if right.has_class("-collapsed"):
            return
        self._panel_pct = max(20, min(40, (self._panel_pct or 20) + delta))
        right.styles.width = f"{self._panel_pct}%"
        self.call_after_refresh(self._refresh_footer)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """停靠区列表选中统一收口（弹窗内的列表已在弹窗内 stop）：权限作答 / clarify /
        / 指令填入 / @ 文件补全。"""
        oid = event.option_list.id
        if oid == "perm-list":
            event.stop()
            self._answer_permission(event.option_id or "no")
            return
        if oid == "clarify-list":
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

    # ---------- 取件 ----------

    def _chat(self) -> _ChatBoard:
        return self.query_one("#chat", _ChatBoard)

    def _prompt(self) -> _CommandInput:
        return self.query_one("#prompt", _CommandInput)

    def _refresh_footer(self) -> None:
        self.query_one(_FooterBar).refresh_data()

    # ---------- 输入回合 ----------

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
        self._chat().anchor()  # 新回合开始：滚回底部并重新钉底
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif opener := self._OPENERS.get(cmd):
            getattr(self, opener)()
        elif self._busy:
            # 回合进行中拒绝 /compact /new 与普通消息：/new 清空会话指针后，本轮后续 add_message
            # 会把回话写进新建会话（见 session.py：current_session 为空时自动 new_session）
            self._reject_busy()
        elif cmd == "/compact":
            self._run_compact()
        elif cmd == "/new":
            if self._manager is not None:
                self._manager.current_session = ""  # 延迟建会话：下条消息到达时自动创建
            self._clear_cards()
        else:
            self._send_user_query(query)


def _default_agent_turn() -> tuple[Callable[[str], None], Callable[[], None], Any]:
    """无 handle_query 时的默认回合：接 core.loop_with_interrupt 的真实 agent。
    延迟导入：--smoke 与离线 UI 演示不必拉起 openai / mcp 依赖。"""
    from core.loop_with_interrupt import make_agent_turn, start_agent_runtime

    runtime = start_agent_runtime()
    return make_agent_turn(runtime), runtime.interrupt, runtime


def run(handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Any = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
        on_interrupt: Optional[Callable[[], None]] = None,
        runtime: Any = None) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    不传 handle_query 时用真实 agent 回合（需已配置模型；离线演示见 core.tui.demo）。
    session_manager 接入后 /new /sessions 可用；runtime 接入后 /compact 可用。
    运行期忽略 SIGINT（Ctrl+C 不关闭应用，退出用 /exit 或 ctrl+q），退出后恢复原处理器。"""
    app = ChatApp(handle_query=handle_query, session_manager=session_manager, banner=banner,
                  on_interrupt=on_interrupt, runtime=runtime)
    _render._APP = app  # run 期间渲染 API（core.tui.render）经此全局桥接进事件循环
    old_sigint = None
    try:
        if threading.current_thread() is threading.main_thread():
            # Ctrl+C 在部分终端（Windows 控制台 / mintty 等）以 SIGINT 送达而非按键事件；
            # Textual 运行期终端处于 raw 模式，按键通道由 BINDINGS 处理（ctrl+c 已空操作）
            old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        app.run()
    finally:
        if old_sigint is not None:
            signal.signal(signal.SIGINT, old_sigint)
        _render._APP = None


if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        from core.tui import smoke

        smoke.main()
    else:
        run()
