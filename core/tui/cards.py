"""消息卡片正文部件：超出上限行数时折叠，点击展开/收回（自 ui_textual.py 拆出）。"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events
from textual.content import Content
from textual.geometry import Size
from textual.strip import Strip
from textual.visual import RenderOptions
from textual.widgets import Static

_TRUNCATED_HINT = "... [truncated {hidden} lines] · click to expand"  # 折叠态末行（点击展开）
_COLLAPSE_HINT = "· click to collapse"  # 展开态末行（点击收回）
_HINT_STYLE = "dim yellow"  # 提示行样式，与正文暗灰区分


class _CappedCardBody(Static):
    """工具卡正文：超出 cap 行时折叠（只渲染前 cap 行 + 1 行提示，点击展开/收回）。

    行数取自 get_content_height：即 Textual 按当前渲染宽度换行后的视觉行数，一行超长
    文本折出的每一行都计入上限（修 "\\n" 计数在长行下低估的问题）。reserved 为固定头行
    数（工具名首行），不参与折叠；-expandable 类（指针 + on_click 路由）随宽度变化在
    on_resize 中同步。"""

    def __init__(self, content: Any, cap: int, reserved: int = 0, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._cap = cap
        self._reserved = reserved
        self._expanded = False
        self._full_height = 0  # 当前宽度下完整内容的视觉行数（get_content_height 记录）

    @property
    def _truncated(self) -> bool:
        return self._full_height - self._reserved > self._cap

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        full = super().get_content_height(container, viewport, width)
        self._full_height = full
        body = max(0, full - self._reserved)  # 扣除固定头行后的正文行数
        return self._reserved + (body + 1 if self._expanded else min(body, self._cap + 1))

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(self._truncated or self._expanded, "-expandable")

    def toggle_expand(self) -> None:
        """截断 ↔ 完整（正文常驻，只切展开标记并按当前宽度重排）"""
        if not self._truncated and not self._expanded:
            return
        self._expanded = not self._expanded
        self.refresh(layout=True)

    def render_line(self, y: int) -> Strip:
        """折叠态末行 / 展开态末行替换为提示行（正文行仍走基类渲染缓存）"""
        if self._expanded and y == self._full_height:
            return self._hint_strip(_COLLAPSE_HINT)
        if not self._expanded and y == self._reserved + self._cap and self._truncated:
            hidden = self._full_height - self._reserved - self._cap
            return self._hint_strip(_TRUNCATED_HINT.format(hidden=hidden))
        return super().render_line(y)

    def _hint_strip(self, text: str) -> Strip:
        content = Content.from_rich_text(Text(text, style=_HINT_STYLE), console=self.app.console)
        options = RenderOptions(self._get_style, self.styles)
        return content.render_strips(self.size.width, 1, self.visual_style, options)[0]
