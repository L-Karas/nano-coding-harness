"""App 显示面：卡片 / Markdown 流 / 状态行 / 欢迎与清板（core.tui.render 的 App 侧落点）。"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual.containers import Vertical
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownStream

from core.tui.cards import _CappedCardBody
from core.tui.theme import _SPINNER_FRAMES


class _StatusLine(Static):
    """状态行：文本 + 可选 spinner 轮播（0.1s，惰性启动、非动画态停表）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._text = ""
        self._spin = False
        self._cursor = 0
        self._timer: Any = None

    def set_text(self, text: str, spin: bool = False) -> None:
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
        """切到 text，返回旧状态 (文本, 是否动画) 供调用方恢复。"""
        previous = (self._text, self._spin)
        self.set_text(text, spin=spin)
        return previous

    def _tick(self) -> None:
        frame = _SPINNER_FRAMES[self._cursor % len(_SPINNER_FRAMES)]
        self._cursor += 1
        self.update(f"{frame} {self._text}")


class _RenderSurface:
    """ChatApp 的渲染面 mixin（方法由 core.tui.render 经事件循环调用）。"""

    # ---------- 卡片 ----------

    def _add_card(self, kind: str, body: Any, cap: Optional[int] = None,
                  reserved: int = 0) -> Static:
        """追加卡片并返回正文 Static；cap 非 None 时按视觉行折叠（reserved = 固定头行数）。"""
        self._set_welcome(False)
        body_w = (_CappedCardBody(body, cap, reserved, classes="card-body", markup=False)
                  if cap is not None else Static(body, classes="card-body", markup=False))
        self._chat().mount(Vertical(body_w, classes=f"card {kind}"))
        return body_w

    def _toggle_expand(self, body_w: Static) -> None:
        body_w.toggle_expand()

    def _add_markdown_card(self, content: str) -> Markdown:
        """追加 Assistant Markdown 卡片（链接点击交系统浏览器）。"""
        self._set_welcome(False)
        md = Markdown(content, open_links=True)
        self._chat().mount(Vertical(md, classes="card assistant"))
        return md

    # ---------- 流式回复 ----------

    def _run_stream_task(self, coro: Any, what: str) -> None:
        """后台跑流协程（调用方必在 App 线程）；失败仅记日志（卡片可能已被清屏移除）。"""

        async def _guarded() -> None:
            try:
                await coro
            except Exception:
                self.log.warning(f"{what} failed", exc_info=True)

        asyncio.get_running_loop().create_task(_guarded())

    def _stop_stream(self) -> None:
        """停掉当前 MarkdownStream 后台任务并复位句柄（回合结束 / 清屏）。"""
        if self._markdown_stream is not None:
            stream, self._markdown_stream = self._markdown_stream, None
            self._run_stream_task(stream.stop(), "markdown stream stop")

    def _stream_update(self, chunk: str) -> None:
        """流式增量：chunk 为本次新增片段（勿传累计全量，会重复追加）。"""
        if not chunk:
            return
        if self._markdown_stream is None:  # 首 chunk：建卡并挂增量渲染流
            self._markdown_stream = Markdown.get_stream(self._add_markdown_card(""))
        self._run_stream_task(self._markdown_stream.write(chunk), "markdown stream update")

    # ---------- 状态行 ----------

    def _set_status_text(self, text: str, spin: bool = False) -> None:
        self.query_one("#status", _StatusLine).set_text(text, spin=spin)

    def _status_swap(self, text: str, spin: bool = False) -> tuple[str, bool]:
        """设置状态并返回旧状态；状态上下文从不嵌套，退出时恢复旧状态即可。"""
        return self.query_one("#status", _StatusLine).swap(text, spin=spin)

    # ---------- 欢迎 / 清板 ----------

    def _set_welcome(self, show: bool) -> None:
        """-welcome 类挂在 #left 上，欢迎标题与聊板互斥显隐（规则见 app.css）。"""
        self.query_one("#left", Vertical).set_class(show, "-welcome")

    def _clear_cards(self) -> None:
        self._stop_stream()  # 先停流再移除卡片，避免残留写入已移除的卡片
        self._chat().remove_children()
        self._chat().anchor()
        self._set_welcome(True)
