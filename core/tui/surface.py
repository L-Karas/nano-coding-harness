"""App 显示面：卡片 / Markdown 流 / 状态行 / 欢迎与清板（core.tui.render 的 App 侧落点）。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from textual.containers import Vertical
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownStream

from core.tui.cards import _CappedCardBody
from core.tui.theme import _SPINNER_FRAMES

# 键帽 emoji（数字 / # / * + VS16 + U+20E3）：Windows Terminal 缺组合圈字形时把 U+20E3
# 画成独立一格，终端走 3 格、Textual 只记 2 格，该行剩余单元格整体右移 1 列
# （分隔线与聊天滚动条看起来「错位」）。入 UI 前降级为基字符，宽度口径与终端一致。
_KEYCAP_CHAR = r"[0-9#*]"  # 键帽基字符：数字 / # / *
_KEYCAP = re.compile(rf"({_KEYCAP_CHAR})\ufe0f?\u20e3")  # 完整序列：捕获组只含基字符
_KEYCAP_TAIL = re.compile(rf"{_KEYCAP_CHAR}\ufe0f?\Z")  # 可能跨 chunk 的尾部前缀


def _strip_keycaps(text: str) -> str:
    """把键帽 emoji 降级为基字符。

    Args:
        text: 原始文本。

    Returns:
        键帽 emoji（如 1️⃣）替换为基字符（1）后的文本；跨 chunk 的半截序列见 _stream_update。
    """
    return _KEYCAP.sub(r"\1", text)


class _StatusLine(Static):
    """状态行：文本 + 可选 spinner 轮播（0.1s，惰性启动、非动画态停表）。"""

    def __init__(self, **kwargs: Any) -> None:
        """初始化状态行。

        Args:
            kwargs: 透传给 Static 的参数。
        """
        super().__init__("", **kwargs)
        self._text = ""
        self._spin = False
        self._cursor = 0
        self._timer: Any = None

    def set_text(self, text: str, spin: bool = False) -> None:
        """设置状态文本；spin 为真时启动 spinner 轮播。

        Args:
            text: 状态文本。
            spin: 是否显示 spinner 动画。
        """
        self._text = text
        self._spin = spin
        if spin:
            self._cursor = 0
            self._tick()  # 立即出首帧（此后由 interval 推进）
            if self._timer is None:
                self._timer = self.set_interval(0.1, self._tick)
        else:
            self.update(text)
            if self._timer is not None:
                self._timer.stop()
                self._timer = None

    def swap(self, text: str, spin: bool = True) -> tuple[str, bool]:
        """切换状态文本。

        Args:
            text: 新状态文本。
            spin: 是否显示 spinner 动画。

        Returns:
            旧状态 (文本, 是否动画)，供调用方恢复。
        """
        previous = (self._text, self._spin)
        self.set_text(text, spin=spin)
        return previous

    def _tick(self) -> None:
        """推进一帧 spinner 并刷新状态文本。"""
        frame = _SPINNER_FRAMES[self._cursor % len(_SPINNER_FRAMES)]
        self._cursor += 1
        self.update(f"{frame} {self._text}")


class _RenderSurface:
    """ChatApp 的渲染面 mixin（方法由 core.tui.render 经事件循环调用）。"""

    # ---------- 卡片 ----------

    def _add_card(
        self,
        kind: str,
        body: Any,
        cap: Optional[int] = None,
        reserved: int = 0,
        tail: Any = None,
    ) -> Static:
        """追加卡片。

        Args:
            kind: 卡片类型。
            body: 卡片正文。
            cap: 折叠行数上限；None 表示不折叠。
            reserved: 固定头行数。
            tail: 固定尾段；不参与折叠，排在正文之后。

        Returns:
            卡片正文 Static。
        """
        self._set_welcome(False)
        body_w = (_CappedCardBody(
            body,
            cap,
            reserved,
            classes="card-body",
            markup=False,
        )
                  if cap is not None else Static(body, classes="card-body", markup=False))
        widgets = [body_w]
        if tail is not None:
            widgets.append(Static(tail, classes="card-body", markup=False))
        self._chat().mount(Vertical(*widgets, classes=f"card {kind}"))
        return body_w

    def _toggle_expand(self, body_w: Static) -> None:
        """切换卡片正文的展开/折叠。

        Args:
            body_w: 卡片正文部件。
        """
        body_w.toggle_expand()

    def _add_markdown_card(self, content: str) -> Markdown:
        """追加 Assistant Markdown 卡片。

        Args:
            content: Markdown 正文。

        Returns:
            新挂载的 Markdown 部件；链接点击交系统浏览器，键帽序列先降级（见 _strip_keycaps）。
        """
        self._set_welcome(False)
        md = Markdown(_strip_keycaps(content), open_links=True)
        self._chat().mount(Vertical(md, classes="card assistant"))
        return md

    # ---------- 通知 ----------

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

    # ---------- 流式回复 ----------

    def _run_stream_task(self, coro: Any, what: str) -> None:
        """后台运行流协程。

        Args:
            coro: 待运行的协程。
            what: 日志中的操作名。

        调用方必在 App 线程；失败仅记日志（卡片可能已被清屏移除）。
        """

        async def _guarded() -> None:
            """执行协程并把异常记为警告日志。"""
            try:
                await coro
            except Exception:
                self.log.warning(f"{what} failed", exc_info=True)

        asyncio.get_running_loop().create_task(_guarded())

    def _stop_stream(self) -> None:
        """停掉当前 MarkdownStream 后台任务并复位句柄（回合结束 / 清屏）。
        尾部未成形的键帽前缀（如半截 1️⃣）按基字符补发，不丢正文。"""
        stream, self._markdown_stream = self._markdown_stream, None
        carry, self._keycap_carry = self._keycap_carry, ""
        if stream is None:
            if carry:  # 整条流只有半截键帽前缀（尚未建卡）：补一张卡，不丢正文
                self._add_markdown_card(carry.removesuffix("\ufe0f"))
            return

        async def _finish() -> None:
            """补发尾部半截键帽前缀后停止流。"""
            if carry:
                await stream.write(carry.removesuffix("\ufe0f"))
            await stream.stop()

        self._run_stream_task(_finish(), "markdown stream stop")

    def _stream_update(self, chunk: str) -> None:
        """追加流式增量。

        Args:
            chunk: 本次新增片段；勿传累计全量，会重复追加。

        尾部半截键帽前缀留到下一片再拼（停流时补发）。
        """
        if not chunk:
            return
        piece = self._keycap_carry + chunk
        self._keycap_carry = ""
        if (tail := _KEYCAP_TAIL.search(piece)) is not None:  # 半截键帽留到下一片再拼
            self._keycap_carry = tail.group()
            piece = piece[: tail.start()]
        piece = _strip_keycaps(piece)
        if not piece:
            return
        if self._markdown_stream is None:  # 首 chunk：建卡并挂增量渲染流
            self._markdown_stream = Markdown.get_stream(self._add_markdown_card(""))
        self._run_stream_task(self._markdown_stream.write(piece), "markdown stream update")

    # ---------- 状态行 ----------

    def _set_status_text(self, text: str, spin: bool = False) -> None:
        """设置状态行文本。

        Args:
            text: 状态文本。
            spin: 是否显示 spinner 动画。
        """
        self.query_one("#status", _StatusLine).set_text(text, spin=spin)

    def _status_swap(self, text: str, spin: bool = False) -> tuple[str, bool]:
        """设置状态并返回旧状态。

        Args:
            text: 新状态文本。
            spin: 是否显示 spinner 动画。

        Returns:
            旧状态 (文本, 是否动画)；状态上下文从不嵌套，退出时恢复旧状态即可。
        """
        return self.query_one("#status", _StatusLine).swap(text, spin=spin)

    # ---------- 欢迎 / 清板 ----------

    def _set_welcome(self, show: bool) -> None:
        """切换欢迎标题与聊板的显隐。

        Args:
            show: 是否显示欢迎标题。

        -welcome 类挂在 #left 上，两者互斥（规则见 app.css）。
        """
        self.query_one("#left", Vertical).set_class(show, "-welcome")

    def _clear_cards(self) -> None:
        """清空聊天卡片并恢复欢迎标题。"""
        self._stop_stream()  # 先停流再移除卡片，避免残留写入已移除的卡片
        self._chat().remove_children()
        self._chat().anchor()
        self._set_welcome(True)
