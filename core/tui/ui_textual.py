"""Textual 全屏对话界面：ChatApp 装配 + run() 启动入口（`python -m core.tui.ui_textual [--smoke]`）。

职责分片：board.py（聊天画板）、turn.py（前台回合）、ask.py + dock.py（权限 / clarify）、
right_panel.py（右栏）、commands.py（/ 指令与消息分派）、interactions.py（交互事件路由）；
弹窗 screens/、右栏 info_panel.py、端口 Adapter render.py、部件取件 parts.py。

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
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static

import core.tui.render as _render  # run() 把 ChatApp 挂到渲染桥接的 _APP 全局
from core.context.session import SessionManager
from core.runtime_state import RUNTIME_STATE, RuntimeState
from core.tui.ask import AskState
from core.tui.board import Board
from core.tui.commands import _CommandFlow
from core.tui.ask_panel import AskPanel
from core.tui.info_panel import _InfoPanel
from core.tui.interactions import _DockInteractions
from core.tui.panels import _ChatBoard, _ChatDock, _Welcome
from core.tui.parts import Parts
from core.tui.right_panel import RightPanel
from core.tui.theme import DEFAULT_SUBTITLE, DEFAULT_TITLE, SECONDARY_TEXT, TERMINAL_TITLE
from core.tui.turn import Turn
from core.tui.widgets import _CommandInput


class _ChatScreen(Screen):
    """主屏：焦点变更一律改道当前交互，点击其他区域不夺走键盘控制
    （权限/澄清挂起 → 对应列表，Other 输入 → 输入条，平时 → 输入栏）。"""

    def set_focus(
        self,
        widget: Widget | None,
        scroll_visible: bool = True,
        from_app_focus: bool = False,
    ) -> None:
        """设置焦点，并把主屏上的焦点改道到当前交互。

        Args:
            widget: 目标部件；为 None 时清空焦点且不改道。
            scroll_visible: 是否滚动到可见区域。
            from_app_focus: 是否由 App 级焦点变更触发。
        """
        if widget is not None and self.app.screen is self:
            widget = self.app.ask_panel.focus_target(widget)  # 权限/clarify 挂起时改道
        super().set_focus(widget, scroll_visible=scroll_visible, from_app_focus=from_app_focus)


def _set_terminal_title(driver: Any, title: str) -> None:
    """设置终端窗口标题。

    Args:
        driver: Textual 终端驱动。
        title: 窗口标题；空串即清空。

    使用 OSC 2；driver 为 None / headless 时为空操作。
    """
    if driver is not None:
        driver.write(f"\x1b]2;{title}\x07")


def _set_terminal_progress(driver: Any, state: int, value: Optional[int] = None) -> None:
    """设置终端任务栏进度。

    Args:
        driver: Textual 终端驱动。
        state: 进度状态：3 不确定（运行中）/ 4 黄色（等待用户输入）/ 0 清除。
        value: 可选百分比。

    使用 OSC 9;4；driver 为 None 时忽略。
    """
    if driver is not None:
        tail = f";{value}" if value is not None else ""
        driver.write(f"\x1b]9;4;{state}{tail}\x07")


class ChatApp(_CommandFlow, _DockInteractions, App):
    """Nano Harness 对话主界面。

    handle_query(query)：每轮消息回调，后台线程执行（不传用真实 agent 回合，见 _default_agent_turn）；
    on_interrupt()：Esc 中断回调（默认 runtime.interrupt）；
    session_manager：提供 new/delete/load_session* / add_message / current_session 的对象；
    runtime：AgentRuntime（/compact 压缩用）。
    """

    CSS_PATH = "app.css"
    TITLE = "Nano Harness"
    SUB_TITLE = "Textual UI"

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """注册 app.css 使用的自定义变量。

        Returns:
            含 $secondary-text（次级文字色）的变量表。
        """
        return {**super().get_theme_variable_defaults(), "secondary-text": SECONDARY_TEXT}

    # ctrl+c 空操作覆盖 Textual 默认的 help_quit（退出用 /exit 或 ctrl+q；复制由更内层绑定处理）
    BINDINGS = [("escape", "deny_permission", "Deny/Interrupt"),  # 权限/clarify 优先，其次中断回合
                ("space", "toggle_clarify", "Toggle clarify selection"),
                ("ctrl+c", "copy_or_ignore", "Ignore")]

    def __init__(
        self,
        handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Optional[SessionManager] = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
        on_interrupt: Optional[Callable[[], None]] = None,
        runtime: Any = None,
        runtime_state: RuntimeState = RUNTIME_STATE,
    ) -> None:
        """初始化对话主界面。

        Args:
            handle_query: 每轮消息回调；省略时接真实 agent 回合。
            session_manager: 会话管理器。
            banner: 欢迎标题与副标题。
            on_interrupt: Esc 中断回调；省略时用 runtime.interrupt；
                handle_query 省略时改用默认 agent 回合的 interrupt。
            runtime: agent 运行时；handle_query 省略时改用默认 agent 运行时。
            runtime_state: 右栏信息面板的数据源。
        """
        super().__init__(ansi_color=True)  # 按终端调色板显示 ANSI 色，避免与终端主题不一致
        self.parts = Parts(self)  # #id ↔ 部件类型的唯一收口（惰性取件）
        if handle_query is None:
            handle_query, on_interrupt, runtime = _default_agent_turn()
        self._handle = handle_query
        self._interrupt = on_interrupt or (runtime.interrupt if runtime is not None else None)
        self._runtime = runtime
        self._runtime_state = runtime_state  # 右栏信息面板的数据源（测试可注入 fresh RuntimeState）
        self._manager = session_manager
        self._banner = banner
        self.board = Board(lambda: self.parts.board, on_welcome=self._set_welcome)
        self.asks = AskState()  # 至多一个挂起询问（权限 / clarify 共用）
        self.ask_panel = AskPanel(self.parts, self.asks)  # 询问视图 → 部件（幂等 reconcile）
        # prompt / status / call_on_app_thread 用 lambda 延迟到调用时解析（测试会替换它们）
        self.turn = Turn(  # 前台回合：busy 的唯一写者
            prompt=lambda: self._prompt(),
            status=lambda *a, **k: self._set_status_text(*a, **k),
            progress=self._progress_sync,
            cancel_asks=self.ask_panel.cancel,
            notify=self._notify,
            call_on_app_thread=lambda fn: self.call_from_thread(fn),
        )
        self.right_panel = RightPanel(  # 折叠态 / 宽度 / 手动展开意图的唯一真值
            self.parts,
            on_layout_changed=lambda: self.call_after_refresh(self._refresh_footer),
        )
        self._progress_state: Optional[tuple[int, Optional[int]]] = None  # 任务栏已写状态（_write_progress 去重）

    def get_default_screen(self) -> Screen:
        """创建默认主屏。

        Returns:
            主屏 _ChatScreen。
        """
        return _ChatScreen(id="_default")

    def action_copy_or_ignore(self) -> None:
        """Ctrl+C 空操作（覆盖基类退出提示）。"""

    def compose(self) -> ComposeResult:
        """组装左栏聊天区与右栏信息面板。

        Yields:
            主界面部件。
        """
        title, subtitle = self._banner
        with Horizontal(id="main"):  # 左 4fr 聊天区 / 右 1fr 信息栏（比例见 app.css）
            with Vertical(id="left"):
                yield _Welcome(title, subtitle)
                yield _ChatBoard(id="chat")
                yield _ChatDock(id="dock", session_tokens=self._session_tokens)
            with Vertical(id="right", classes="-collapsed"):  # 默认折叠，三区非空时 right_panel.sync 自动展开
                with Vertical(id="info-tab"):  # 分栏线旁的 »/« 折叠开关
                    tab = Static("«", id="info-tab-glyph")
                    tab.tooltip = "Expand info panel"
                    yield tab
                yield _InfoPanel(id="info-panel", state=self._runtime_state,
                                 on_sections_changed=self.right_panel.sync)

    def on_mount(self) -> None:
        """挂载后聚焦输入框、钉底聊板并启动状态轮询。"""
        self._prompt().focus()
        self.parts.board.anchor()  # 钉底：溢出时保持贴底，用户上滚解除、滚回底部恢复
        self._set_welcome(True)
        self._refresh_footer()
        _set_terminal_title(self._driver, TERMINAL_TITLE)
        self.set_interval(1.0, self._progress_sync)  # 后台任务 / 子代理 / 自动回合的兜底重估

    def on_unmount(self) -> None:
        """退出（App Unmount，driver 尚未 close）时清空窗口标题与任务栏进度，交还 shell 默认状态。"""
        _set_terminal_title(self._driver, "")
        _set_terminal_progress(self._driver, 0)  # 绕过去重兜底，确保交还 shell

    # ---------- 终端任务栏进度（OSC 9;4） ----------

    def _write_progress(self, state: int, value: Optional[int] = None) -> None:
        """写入任务栏进度。

        Args:
            state: 进度状态。
            value: 可选百分比。

        driver 缺失时忽略；与上次相同则不重复写（tick 可任意频繁调用）。
        """
        if self._driver is None or (state, value) == self._progress_state:
            return
        self._progress_state = (state, value)
        _set_terminal_progress(self._driver, state, value)

    def _progress_working(self) -> None:
        """运行中（前台回合 / 后台任务 / 子代理 / 自动回合）：任务栏不确定进度（动态条）。"""
        self._write_progress(3)

    def _progress_waiting(self) -> None:
        """等待用户作答（权限确认 / clarify）：任务栏满格黄色静态条（state 4 + 100%）。"""
        self._write_progress(4, 100)

    def _progress_clear(self) -> None:
        """无进行中的工作：清除任务栏进度。"""
        self._write_progress(0)

    def _background_active(self) -> bool:
        """判断是否有在跑的后台任务 / 子代理。

        Returns:
            存在运行中的后台任务或子代理时为 True；子代理条目只在运行期存在，Todos 不算。
        """
        return self._runtime_state.snapshot().has_running_background()

    def _progress_sync(self) -> None:
        """任务栏进度唯一决策点（等待作答 > 运行中 > 清除）：前台回合 Turn.busy、runtime 回合、
        后台任务、子代理任一在跑即动态条；权限 / clarify 挂起时黄色。每次实时重算并以写去重，
        可被 1s 兜底 tick 反复调用。"""
        if self.asks.view() is not None:
            self._progress_waiting()
        elif self.turn.busy or self._agent_running() or self._background_active():
            self._progress_working()
        else:
            self._progress_clear()

    def on_resize(self, event: events.Resize) -> None:
        """终端宽度变化时重算页脚对齐。

        Args:
            event: 尺寸变化事件。
        """
        self._refresh_footer()  # 页脚右对齐随终端宽度实时重算

    def on_click(self, event: events.Click) -> None:
        """处理卡片与右栏折叠标签的点击。

        Args:
            event: 点击事件。

        卡片正文点击 = 展开/收起；»/« 点击 = 折叠/展开右栏（分区点击由 _InfoPanel 处理）。
        """
        target = event.widget
        if target.has_class("-expandable"):
            target.toggle_expand()
            return
        if target.id != "info-tab-glyph":
            return
        collapsed = not self.right_panel.collapsed
        self.right_panel.set_collapsed(collapsed, manual=not collapsed)

    # ---------- 右栏折叠 / 展开与调宽（Ctrl+←/→，绑定见 _CommandInput.BINDINGS） ----------

    def action_narrow_info_panel(self) -> None:
        """Ctrl+→：收窄右栏一档；低于 20% 即折叠。"""
        self.right_panel.narrow()

    def action_widen_info_panel(self) -> None:
        """Ctrl+←：折叠态展开到（或恢复到）上次宽度；已展开则加宽一档（封顶 40%）。"""
        self.right_panel.widen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """处理停靠区列表选中。

        Args:
            event: 选项选中事件。

        统一收口（弹窗内的列表已在弹窗内 stop）：权限作答 / clarify / / 指令填入 /
        @ 文件补全。
        """
        oid = event.option_list.id
        if oid == "perm-list":
            event.stop()
            self._answer(event.option_id or "no")
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

    # ---------- 状态行 / 通知 / 欢迎态 ----------

    def _set_status_text(self, text: str, spin: bool = False) -> None:
        """设置状态行文本。

        Args:
            text: 状态文本。
            spin: 是否显示 spinner 动画。
        """
        self.parts.status.set_text(text, spin=spin)

    def _status_swap(self, text: str, spin: bool = False) -> tuple[str, bool]:
        """设置状态并返回旧状态。

        Args:
            text: 新状态文本。
            spin: 是否显示 spinner 动画。

        Returns:
            旧状态 (文本, 是否动画)；状态上下文从不嵌套，退出时恢复旧状态即可。
        """
        return self.parts.status.swap(text, spin=spin)

    def _notify(self, message: str, title: str = "", severity: str = "information") -> None:
        """显示即时 toast。

        Args:
            message: 通知正文。
            title: 通知标题。
            severity: 严重级别。

        正文按纯文本渲染，避免工具输出 / 异常文本里的方括号被 Rich 解析；
        App.notify 线程安全，worker 线程可直接调用。
        """
        self.notify(message, title=title, severity=severity, markup=False)

    def _set_welcome(self, show: bool) -> None:
        """切换欢迎标题与聊板的显隐。

        Args:
            show: 是否显示欢迎标题。

        -welcome 类挂在 #left 上，两者互斥（规则见 app.css）。
        """
        self.parts.left.set_class(show, "-welcome")

    # ---------- 取件 ----------

    def _prompt(self) -> _CommandInput:
        """取输入框。

        Returns:
            #prompt 输入框。
        """
        return self.parts.prompt

    def _refresh_footer(self) -> None:
        """立即刷新页脚数据。"""
        self.parts.footer.refresh_data()

    def _session_tokens(self) -> int:
        """当前会话 token 估算数。

        Returns:
            估算值；未接 SessionManager / 取值失败时为 0。
        """
        manager = self._manager
        if manager is None:
            return 0
        try:
            return manager.load_session_tokens()
        except Exception:
            return 0

    # ---------- 输入回合 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理输入框提交：clarify 的 Other 输入或普通用户回合。

        Args:
            event: 提交事件。
        """
        view = self.asks.view()
        if view is not None and view.other_mode:  # clarify 的 Other 输入：不当作新回合
            self._after_ask(self.ask_panel.submit_other(event.value))  # 空提交退回选项列表
            return
        query = event.value.strip()
        self._prompt().text = ""
        if not query:
            return
        self.parts.board.anchor()  # 新回合开始：滚回底部并重新钉底
        self._dispatch_query(query)


def _default_agent_turn() -> tuple[Callable[[str], None], Callable[[], None], Any]:
    """构造默认 agent 回合。

    Returns:
        (handle_query, interrupt, runtime) 三元组；延迟导入，--smoke 与离线 UI 演示
        不必拉起 openai / mcp 依赖。
    """
    from core.agent_loop import make_agent_turn, start_agent_runtime

    runtime = start_agent_runtime()
    return make_agent_turn(runtime), runtime.interrupt, runtime


def run(
    handle_query: Optional[Callable[[str], None]] = None,
    session_manager: Any = None,
    banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE),
    on_interrupt: Optional[Callable[[], None]] = None,
    runtime: Any = None,
) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    Args:
        handle_query: 每轮消息回调；省略时用真实 agent 回合。
        session_manager: 会话管理器；接入后 /new /sessions 可用。
        banner: 欢迎标题与副标题。
        on_interrupt: Esc 中断回调；handle_query 省略时改用默认 agent 回合的 interrupt。
        runtime: agent 运行时；接入后 /compact 可用；handle_query 省略时改用默认运行时。

    不传 handle_query 时需已配置模型（离线演示见 smoke.demo）。
    运行期忽略 SIGINT（Ctrl+C 不关闭应用，退出用 /exit 或 ctrl+q），退出后恢复原处理器。
    """
    from core.bootstrap import bootstrap
    bootstrap()

    app = ChatApp(
        handle_query=handle_query,
        session_manager=session_manager,
        banner=banner,
        on_interrupt=on_interrupt,
        runtime=runtime,
    )
    with _render.attach(app):  # 端口 Adapter 的唯一挂载点（run_test 走同一入口）
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


if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        from core.bootstrap import bootstrap
        from smoke import main as smoke_main

        bootstrap()
        smoke_main()
    else:
        run()
