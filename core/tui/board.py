"""聊天画板 Adapter：卡片挂载 / 工具卡并回 / 流式 Markdown / 清板。

宿主只提供两个依赖：滚动容器取件与欢迎态回调；渲染规则（卡片长什么样）在 core.tui.blocks。

不变式：
- 只在 App 线程调用（线程桥接由 core.tui.render 负责）；
- `end_stream` 幂等，尾部半截键帽补发；`stream` 首片自建卡、空片 no-op；
- `merge` 对同一 call_id 只生效一次，未登记的 call_id 独立成卡；
- `clear` 先停流再删卡，最后恢复欢迎态。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Optional

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownStream

from core.tui.blocks import Block, split_trailing_keycap, strip_keycaps
from core.tui.cards import _CappedCardBody


class Board:
    """聊天画板：把 Block 挂成卡片，并按 call_id 把工具结果并回调用卡。"""

    def __init__(
        self,
        scroll: Callable[[], VerticalScroll],
        *,
        on_welcome: Callable[[bool], None],
    ) -> None:
        """初始化画板。

        Args:
            scroll: 取聊天滚动容器；惰性求值，允许在挂载前构造。
            on_welcome: 欢迎态回调；追加内容时传 False，清板时传 True。
        """
        self._scroll = scroll
        self._on_welcome = on_welcome
        self._markdown_stream: Optional[MarkdownStream] = None  # 流式卡片增量渲染句柄
        self._pending_tool_cards: dict[str, tuple[Vertical, _CappedCardBody]] = {}  # 等待结果的调用卡
        self._keycap_carry = ""  # 跨 chunk 的半截键帽前缀（见 stream）

    # ---------- 卡片 ----------

    def add(self, block: Block, *, spin: bool = True) -> None:
        """追加一张卡片。

        Args:
            block: 展示值。
            spin: 工具调用卡在等待结果期间是否轮播 spinner；非工具卡忽略。
        """
        self._on_welcome(False)
        if block.kind == "assistant":  # Markdown 部件只吃 str，单独成卡
            self._mount_markdown(block.body)
            return
        card = Vertical(classes=f"card {block.kind}")
        self._scroll().mount(card)
        is_tool = block.kind == "tool" and bool(block.tool_call_id)
        widgets = self._body_widgets(block, spin=spin and is_tool)
        card.mount(*widgets)
        if is_tool:  # 回放场景 spin=False 也要登记，否则结果无法并回
            self._pending_tool_cards[block.tool_call_id] = (card, widgets[0])

    def merge(self, block: Block) -> None:
        """把结果段并回对应调用卡；无对应卡时独立成卡。

        Args:
            block: 结果展示值；`call_id` 为空或无登记项时按独立卡渲染。
        """
        entry = self._pending_tool_cards.pop(block.tool_call_id, None) if block.tool_call_id else None
        if entry is None:
            self.add(block)
            return
        card, body_widget = entry
        # 结果到达：标题行不再轮播；手工构造的 tool 卡可能没有 spinner 正文
        stop_spin = getattr(body_widget, "stop_spin", None)
        if stop_spin is not None:
            stop_spin()
        card.remove_class("tool")
        card.add_class(block.kind)
        card.mount(*self._body_widgets(block, classes="card-body -merged"))

    def _mount_markdown(self, content: str) -> Markdown:
        """追加 Assistant Markdown 卡片（调用方负责收起欢迎态）。

        Args:
            content: Markdown 正文。

        Returns:
            新挂载的 Markdown 部件；链接点击交系统浏览器。
        """
        md = Markdown(content, open_links=True)
        self._scroll().mount(Vertical(md, classes="card assistant"))
        return md

    @staticmethod
    def _body_widgets(
        block: Block,
        *,
        classes: str = "card-body",
        spin: bool = False,
    ) -> list[Static]:
        """按「正文段 + 可选固定尾段」组装卡片的子部件。

        Args:
            block: 展示值。
            classes: 正文段的 CSS 类。
            spin: 正文段是否在标题行前轮播 spinner。

        Returns:
            可直接 mount 进卡片容器的部件列表；`widgets[0]` 恒为正文段。
        """
        body = (_CappedCardBody(block.body, block.max_lines, block.head_lines,
                                spin=spin, classes=classes, markup=False)
                if block.max_lines is not None else Static(block.body, classes=classes, markup=False))
        widgets: list[Static] = [body]
        if block.tail is not None:
            widgets.append(Static(block.tail, classes="card-body", markup=False))
        return widgets

    # ---------- 流式回复 ----------

    def stream(self, chunk: str) -> None:
        """追加流式增量。

        Args:
            chunk: 本次新增片段；勿传累计全量，会重复追加。

        首片自动建卡；尾部半截键帽前缀留到下一片再拼（停流时补发）。
        """
        if not chunk:
            return
        piece, self._keycap_carry = split_trailing_keycap(self._keycap_carry + chunk)
        piece = strip_keycaps(piece)
        if not piece:
            return
        if self._markdown_stream is None:
            self._on_welcome(False)
            self._markdown_stream = Markdown.get_stream(self._mount_markdown(""))
        self._spawn(self._markdown_stream.write(piece), "markdown stream update")

    def end_stream(self) -> None:
        """停掉当前 MarkdownStream 后台任务并复位句柄（回合结束 / 清屏）。

        尾部未成形的键帽前缀（如半截 1️⃣）按基字符补发，不丢正文。
        """
        stream, self._markdown_stream = self._markdown_stream, None
        carry, self._keycap_carry = self._keycap_carry, ""
        if stream is None:
            if carry:  # 整条流只有半截键帽前缀（尚未建卡）：补一张卡，不丢正文
                self.add(Block("assistant", carry.removesuffix("\ufe0f")))
            return

        async def _finish() -> None:
            """补发尾部半截键帽前缀后停止流。"""
            if carry:
                await stream.write(carry.removesuffix("\ufe0f"))
            await stream.stop()

        self._spawn(_finish(), "markdown stream stop")

    # ---------- 清板 ----------

    def clear(self) -> None:
        """清空聊天卡片并恢复欢迎态。"""
        self.end_stream()  # 先停流再移除卡片，避免残留写入已移除的卡片
        self._pending_tool_cards.clear()  # 卡片已移除，登记项不可再指向旧卡
        self._scroll().remove_children()
        self._scroll().anchor()
        self._on_welcome(True)

    # ---------- 后台流任务 ----------

    def _spawn(self, coro: Any, label: str) -> None:
        """后台运行流协程。

        Args:
            coro: 待运行的协程。
            label: 日志中的操作名。

        调用方必在 App 线程；失败仅记日志（卡片可能已被清屏移除）。
        """

        async def _guarded() -> None:
            """执行协程并把异常记为警告日志。"""
            try:
                await coro
            except Exception:
                self._scroll().app.log.warning(f"{label} failed", exc_info=True)

        asyncio.get_running_loop().create_task(_guarded())
