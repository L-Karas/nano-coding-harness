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
- demo.py     无 handle_query 时的内置演示 Agent
- utils.py    终端环境信息与指令表（原有）

本模块保留：ChatApp（compose 只留左右分栏骨架，分区部件见 panels.py）、
run() 入口，以及 `python -m core.tui.ui_textual` 演示与 --smoke 自检。

运行演示（无需 LLM/网络）:
    python -m core.tui.ui_textual

接入真实 Agent（需在 main.py 侧做等价替换，本模块不代劳）:
    from core.tui.ui_textual import run
    run(handle_query=agent_turn, session_manager=SESSION_MANAGER)
    # agent_turn(query) 在后台线程执行；内部渲染调用 core.tui.render 的同名函数（线程安全）。

线程模型:
    - App 事件循环跑主线程；每轮用户输入在独立后台线程里调用 handle_query。
    - 渲染函数（core.tui.render）可从任意线程调用（App 线程内直接执行，
      其它线程经 call_from_thread 桥接）。
    - ask_permission 只能在非 App 线程调用（会阻塞等待用户在输入条中作答）。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Label, ListView, Markdown, OptionList, Static, TextArea
from textual.widgets.markdown import MarkdownStream

import core.tui.render as _render  # run() 期间把 ChatApp 实例挂到渲染桥接的 _APP 全局（见 run()）
from core.tui.demo import _demo_agent
from core.tui.render import (
    render_background_notification,
    render_sessions,
    render_session_history,
    render_tool_result,
    render_user_input,
)
from core.tui.theme import (
    DEFAULT_SUBTITLE,
    DEFAULT_TITLE,
    _PLACEHOLDER,
    _SPINNER_FRAMES,
    _markup,
)
from core.tui.panels import _ChatBoard, _ChatDock, _TitleBar
from core.tui.utils import SLASH_COMMANDS, current_git_branch, working_directory
from core.tui.widgets import SessionPickerScreen, _CommandInput, _match_project_entries


class ChatApp(App):
    """Nano-Harness 对话主界面：标题栏 + 卡片式聊天记录 + 状态行 + 输入框。

    handle_query(query): 每轮用户消息回调，在后台线程执行（可调用本模块渲染函数）。
    session_manager: 可选，提供 new_session / delete_session / load_session_list /
                     load_session / add_message / current_session 的对象（如 core.session 的 SESSION_MANAGER）。
    """

    CSS_PATH = "app.css"  # 同目录样式表，路径相对本模块文件
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
        self._active_stream: Optional[Markdown] = None
        self._markdown_stream: Optional[MarkdownStream] = None  # 流卡片增量渲染句柄（get_stream 后台合并解析）
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
        with Horizontal(id="main"):  # 左右分栏：左 4fr 现有聊天界面，右 1fr 预留（比例见 app.css）
            with Vertical(id="left"):
                yield _TitleBar(title, subtitle)
                yield _ChatBoard(id="chat")
                yield _ChatDock(id="dock")
            with Vertical(id="right"):  # 预留右栏：Information 标题 + 后续内容
                yield Static("Information", id="right-title")

    def on_mount(self) -> None:
        self._prompt().focus()
        self._chat().anchor()  # 钉底：溢出时新内容由 compositor 布局自动保持贴底；用户上滚解除、滚回底部自动恢复（机制见 panels._ChatBoard docstring）
        self._refresh_footer()
        self.set_interval(10.0, self._refresh_footer)  # 轮询刷新 cwd/git（用户可能另开终端切目录/分支）；单次 ~毫秒级

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """点击候选列表项：/ 指令直接接受；@ 文件列表把选中路径补全进输入（不发送）"""
        event.stop()
        idx = event.list_view.index
        if idx is None:
            return
        prompt = self.query_one("#prompt", _CommandInput)
        if event.list_view.id == "file-suggest":
            if not prompt._apply_file_candidate(idx):
                return
        else:
            if not 0 <= idx < len(SLASH_COMMANDS):
                return
            cmd = SLASH_COMMANDS[idx]  # 子项与 SLASH_COMMANDS 一一对应（见 compose）
            prompt.text = cmd
            prompt.cursor_location = (0, len(cmd))
        prompt.focus()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _status(self) -> Static:
        return self.query_one("#status", Static)

    def _prompt(self) -> TextArea:
        return self.query_one("#prompt", TextArea)

    def _refresh_footer(self) -> None:
        """刷新最底行文本：当前工作目录 (git 分支)；非 git 仓库只显示目录（如 E:/AI-Programs/nano-harness (tui-modify)）"""
        cwd = working_directory()
        branch = current_git_branch()
        label = f"{cwd} ({branch})" if branch else cwd
        self.query_one("#footer", Static).update(Text(label, no_wrap=True, style="#64748b"))

    # ---------- 卡片 ----------

    def _add_card(self, kind: str, body: Any) -> Static:
        """追加一张卡片，返回 body Static（流式更新用）"""
        body_w = Static(body, classes="card-body", markup=False)
        self._chat().mount(Vertical(body_w, classes=f"card {kind}"))
        return body_w

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
        self._chat().anchor()  # 新回合开始：滚回底部并重新钉底（用户可能正浏览历史）
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/sessions":
            self._open_sessions()
        elif self._busy:
            # 回合进行中拒绝 /new 与普通消息：/new 若清空会话指针，本轮后续 add_message
            # 会把回话写进新建的会话（见 session.py：current_session 为空时自动 new_session）
            render_background_notification("上一轮仍在运行，请稍候…", title="⏳ Busy")
        elif cmd == "/new":
            # 延迟创建：仅丢弃当前会话指针，下一条用户消息到达时由 add_message() 自动建新会话，避免空会话
            if self._manager is not None:
                self._manager.current_session = ""
            self._clear_cards()
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
        self._stop_stream()
        self._chat().anchor()  # 空列表无滚动位置；重新钉底供后续内容/历史回放跟随


def run(handle_query: Optional[Callable[[str], None]] = None,
        session_manager: Any = None,
        banner: tuple[str, str] = (DEFAULT_TITLE, DEFAULT_SUBTITLE)) -> None:
    """启动 Textual 对话界面（阻塞直到退出）。

    不传 handle_query 时使用内置演示 Agent（流式 Markdown + 工具调用 + 权限确认等全流程演示，
    query 以 "sudo " 开头会触发权限确认）。
    传入 session_manager（如 core.session 的 SESSION_MANAGER）后 /new /sessions 可用。
    """
    app = ChatApp(handle_query=handle_query, session_manager=session_manager, banner=banner)
    _render._APP = app  # run 期间渲染 API（core.tui.render）经此全局桥接进事件循环
    try:
        app.run()
    finally:
        _render._APP = None


# ======================================================================
# 内置演示与自检：`python -m core.tui.ui_textual` 演示（对齐原 ui.py __main__ 的演示范围）
# ======================================================================

if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        async def _smoke() -> None:
            app = ChatApp(handle_query=_demo_agent)
            _render._APP = app  # 冒烟不经 run()：直接把实例挂到渲染桥接全局
            try:
                async with app.run_test() as pilot:
                    async def settle(pilot, times=10, delay=0.02) -> None:
                        """等 UI 稳定：连做几次短 pause 让事件循环推进（异步挂载/渲染完成）"""
                        for _ in range(times):
                            await pilot.pause(delay)

                    async def type_query(prompt, pilot, text) -> None:
                        """程序化输入并刷新补全候选（赋值不触达键入事件路径，须手动刷新）"""
                        prompt.text = text
                        prompt.cursor_location = (0, len(text))
                        prompt._refresh_suggestions()
                        await settle(pilot)

                    await pilot.pause(0.2)
                    prompt = app.query_one("#prompt", _CommandInput)
                    left_w = app.query_one("#left").region.width  # 左栏宽（4:1 分栏后为终端 80%）
                    # 输入框宽度应占满左栏宽度（不随内容变化）
                    assert prompt.size.width >= left_w - 10, f"输入框宽度未占满左栏: {prompt.size.width}"
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
                    await settle(pilot, 10, 0.05)
                    assert app._exception is None, f"渲染异常: {app._exception}"
                    cards = list(app.query("#chat .card"))
                    assert len(cards) >= 5, f"卡片数量不足: {len(cards)}"
                    # 消息卡片宽度 = 左栏 - 1（内容超出视口时右侧滑块占 1 列）；无溢出时仍占满左栏
                    assert left_w - 2 <= cards[0].region.width <= left_w, \
                        f"卡片宽度异常: {cards[0].region.width} (左栏 {left_w})"
                    assert any("assistant" in c.classes for c in cards), "缺少 Assistant 卡片"
                    # 增量流式自检：每 chunk 只传新增片段 → 内容逐字拼接，不丢不重
                    app._stop_stream()
                    parts = ["## 标题\n", "第一段文字\n\n", "```python\nprint(1)\n```\n", "结尾"]
                    for p in parts:
                        app._stream_update(p)
                        await settle(pilot)
                    md = list(app.query("#chat .card.assistant Markdown"))[-1]  # 本轮新卡（demo 卡在前）
                    assert md.source == "".join(parts), \
                        f"增量流式内容不一致: {md.source!r}"
                    app._stop_stream()  # 停掉本轮 MarkdownStream 后台任务
                    await settle(pilot)
                    print("[smoke] markdown incremental stream OK (chunk append, no drop)")
                    chat = app.query_one("#chat", VerticalScroll)
                    # 卡片 height:auto 后内容超出视口才可滚动；回归 1fr 均分时 max_scroll_y 恒为 0
                    assert chat.max_scroll_y >= 1, "卡片未按内容自适应高度，消息列表不可滚动"
                    # 右侧滑块：内容溢出时必须可见（曾 scrollbar-size-vertical:0 隐藏）；1 列宽便于拖动拇指浏览历史
                    assert chat.show_vertical_scrollbar, "内容溢出时未显示纵向滑块"
                    assert chat.styles.scrollbar_size_vertical == 1, "滑块宽度应为 1 列"
                    await settle(pilot, 5)
                    assert chat.vertical_scrollbar.region.height >= 1, "滑块未渲染出可见区域"
                    # 滚动钉底：回合结束后仍钉在底部（无用户滚动，anchor 不应解除）
                    assert chat._anchored and not chat._anchor_released, \
                        f"回合结束应保持钉底: anchored={chat._anchored} released={chat._anchor_released}"
                    # 浏览历史：用户上滚（等价滚轮上滚 / 拖滑块）→ 解除钉底，追加内容不再拉回视口；
                    # 回到底部 → 自动恢复钉底（后续流式继续跟随）
                    chat.scroll_up()
                    await pilot.pause(0.1)
                    assert chat._anchor_released, "用户上滚后应解除钉底"
                    pos = chat.scroll_y
                    render_user_input("browse-mid-stream")  # 追加新卡片
                    await settle(pilot, 10, 0.05)
                    assert chat.scroll_y == pos, f"浏览中追加内容不应拉动视口: {chat.scroll_y} != {pos}"
                    chat.scroll_end(animate=False, immediate=True)
                    await pilot.pause(0.1)
                    assert not chat._anchor_released, "滚回底部后应恢复钉底"
                    print("[smoke] scrollbar visible + anchor browse/repin OK")
                    # 指令补全：/ 前缀输入时输入框下方弹出候选 ListView（仅此时可见），↑/↓+Tab/Enter 接受；其它输入不显示
                    prompt.focus()
                    suggest = app.query_one("#cmd-suggest", ListView)
                    prompt.text = "/"
                    await settle(pilot)
                    assert suggest.styles.display != "none", "输入 / 未弹出候选 ListView"
                    assert prompt._candidates == ["/new", "/sessions", "/exit"], prompt._candidates
                    visible = [c for c in suggest.children if c.styles.display != "none"]
                    assert len(visible) == 3, f"候选条数错误: {len(visible)}"
                    await pilot.press("down")
                    await pilot.press("tab")
                    assert prompt.text == "/sessions", f"Tab 接受高亮失败: {prompt.text!r}"
                    await settle(pilot, 5)
                    assert suggest.styles.display == "none", "完整指令后列表应隐藏"
                    prompt.text = "/s"
                    await settle(pilot)
                    assert prompt._candidates == ["/sessions"], prompt._candidates
                    visible = [c for c in suggest.children if c.styles.display != "none"]
                    assert len(visible) == 1, f"过滤候选条数错误: {len(visible)}"
                    await pilot.press("enter")  # Enter 应用高亮项并提交
                    await settle(pilot, 20)
                    assert not app._busy, "Enter 应提交补全后的 /sessions（走会话分支，不起 agent 回合）"
                    bodies = [str(w.render()) for w in app.query(".card-body")]
                    assert any("未接入 SessionManager" in b for b in bodies), "Enter 未提交 /sessions"
                    prompt.text = "/xyz"
                    await settle(pilot)
                    assert suggest.styles.display == "none", "无匹配不应显示列表"
                    prompt.text = "hello world"
                    await settle(pilot)
                    assert suggest.styles.display == "none", "普通消息不应显示列表"
                    print(f"[smoke] OK, {len(cards)} cards rendered, scrollable, listview completion OK")

                    # @ 文件补全：空白/行首后的 @ 弹 ListView 展示同级文件（目录以 / 结尾）；
                    # 输入前缀匹配过滤；Tab/Enter 选中把 @完整路径 补进输入（不发送）；完整文件名后列表隐藏
                    prompt.focus()
                    flv = app.query_one("#file-suggest", ListView)
                    assert flv.styles.display == "none", "未输入 @ 不应显示文件列表"
                    await type_query(prompt, pilot, "看看 @core/tu")
                    expected = _match_project_entries("core/tu")
                    assert prompt._file_candidates == expected and expected, prompt._file_candidates
                    assert flv.styles.display != "none", "输入 @ 前缀未弹出文件列表"
                    shown = [str(it.query_one(Label).render()) for it in flv.children]
                    assert shown == expected, f"列表展示与匹配不一致: {shown} != {expected}"
                    assert all(c.endswith("/") for c in expected), \
                        f"同级规则：只应展示目录 core/tui/，实际 {expected}"
                    await pilot.press("tab")  # 选中 core/tui/ → 补全为 @core/tui/ 并展开其内容
                    await settle(pilot)
                    assert prompt.text == "看看 @core/tui/", prompt.text
                    inner = _match_project_entries("core/tui/")
                    assert prompt._file_candidates == inner and inner, prompt._file_candidates
                    assert all(c.startswith("core/tui/") for c in inner), \
                        "进入目录后应展示完整路径"
                    shown = [str(it.query_one(Label).render()) for it in flv.children]
                    assert shown == inner, f"展开目录展示不一致: {shown} != {inner}"
                    # 无 / 的 query：全树相似匹配（名称包含 ui），命中深层文件 → 展示完整路径
                    await type_query(prompt, pilot, "看 @ui")
                    fuzzy = _match_project_entries("ui")
                    assert prompt._file_candidates == fuzzy and len(fuzzy) >= 2, prompt._file_candidates
                    assert fuzzy[0] == "core/tui/"  # 短路径在前
                    assert "core/tui/ui_textual.py" in fuzzy, fuzzy
                    assert all(c.rstrip("/").rsplit("/", 1)[-1].lower().find("ui") >= 0 for c in fuzzy), \
                        f"相似匹配应只含名称带 ui 的条目: {fuzzy}"
                    shown = [str(it.query_one(Label).render()) for it in flv.children]
                    assert shown == fuzzy, f"列表展示与相似匹配不一致: {shown} != {fuzzy}"
                    # 大小写不敏感
                    await type_query(prompt, pilot, "看 @TUI")
                    assert prompt._file_candidates == ["core/tui/"], prompt._file_candidates
                    await pilot.press("enter")  # 选中目录：补全 @core/tui/ 并展开其同级内容
                    await settle(pilot)
                    assert prompt.text == "看 @core/tui/", prompt.text
                    assert prompt._file_candidates == _match_project_entries("core/tui/"), \
                        prompt._file_candidates
                    await type_query(prompt, pilot, "@zz_not_exists")  # 无匹配 → 列表隐藏
                    assert prompt._file_candidates == [] and flv.styles.display == "none"
                    await type_query(prompt, pilot, "改 @core/tui/util")
                    assert prompt._file_candidates == ["core/tui/utils.py"], prompt._file_candidates
                    await pilot.press("enter")  # 选中文件：补全，不发送
                    await settle(pilot)
                    assert prompt.text == "改 @core/tui/utils.py", prompt.text
                    assert flv.styles.display == "none", "补全为完整文件名后列表应隐藏"
                    assert not app._busy, "Enter 选中 @ 文件不应提交回合"
                    prompt.text = "/new"  # 复位（指令提交不触发 agent 回合）
                    await pilot.press("enter")
                    await settle(pilot)
                    assert not app._busy and flv.styles.display == "none"
                    print("[smoke] @ file completion OK: same-level list, / drill-down, full-path insert")

                    # /new 延迟建会话：只清空 current_session 指针，不立即 new_session（防空会话）；
                    # 忙碌中 /new 被拒绝（否则本轮后续 add_message 会把回话写进新建会话）
                    class _FakeMgr:
                        def __init__(self):
                            self.current_session = "session-old.jsonl"
                            self.created = 0

                        def new_session(self):
                            self.created += 1
                            self.current_session = f"session-new-{self.created}.jsonl"

                    fake = _FakeMgr()
                    app._manager = fake
                    prompt.text = "/new"
                    await pilot.press("enter")
                    await settle(pilot)
                    assert fake.created == 0 and fake.current_session == "", \
                        "/new 应立即创建会话（延迟到首条消息，由 add_message 建）"
                    assert not app._busy
                    app._busy = True  # 模拟回合进行中：/new 应提示并保持会话指针不动
                    prompt.text = "/new"
                    await pilot.press("enter")
                    await settle(pilot)
                    assert fake.created == 0 and fake.current_session == "", \
                        "忙碌中 /new 不应清空会话指针"
                    bodies = [str(w.render()) for w in app.query(".card-body")]
                    assert any("上一轮仍在运行" in b for b in bodies), "忙碌中 /new 应提示"
                    app._busy = False
                    app._manager = None
                    app._clear_cards()
                    print("[smoke] /new defers session creation until first message OK")

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
                    await settle(pilot)
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
                    # 末行 = 最旧会话（列表新到旧排列，order[-1] 即末尾行）
                    assert olist.get_option_at_index(149).id == order[-1].id
                    await pilot.press("escape")
                    await settle(pilot)
                    assert app._exception is None, f"渲染异常: {app._exception}"
                    print("[smoke] sessions picker OK: 150 sessions, all reachable, no clipping")
            finally:
                _render._APP = None

        asyncio.run(_smoke())
    else:
        run()
