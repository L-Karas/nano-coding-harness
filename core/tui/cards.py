"""消息卡片正文部件：超出上限行数时折叠，点击展开/收回。"""

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
    """卡片正文：超出 cap 行时折叠（前 cap 行 + 1 行提示，点击展开/收回）。

    行数取自 get_content_height（按渲染宽度换行后的视觉行，长行折出的行同样计入）；
    reserved 为固定头行数，不参与折叠；-expandable 类随宽度变化在 on_resize 中同步。"""

    def __init__(
        self,
        content: Any,
        cap: int,
        reserved: int = 0,
        **kwargs: Any,
    ) -> None:
        """初始化折叠正文。

        Args:
            content: 传给 Static 的正文内容。
            cap: 折叠时最多显示的正文视觉行数。
            reserved: 固定头行数，不参与折叠。
            kwargs: 透传给 Static 的其余参数。
        """
        super().__init__(content, **kwargs)
        self._cap = cap
        self._reserved = reserved
        self._expanded = False
        self._full_height = 0  # 当前宽度下完整内容的视觉行数（get_content_height 记录）

    @property
    def _truncated(self) -> bool:
        """正文是否超过折叠上限。

        Returns:
            当前宽度下正文行数大于 cap 时为 True。
        """
        return self._full_height - self._reserved > self._cap

    def get_content_height(
        self,
        container: Size,
        viewport: Size,
        width: int,
    ) -> int:
        """按折叠状态返回正文高度，并记录当前宽度下的完整高度。

        Args:
            container: 容器尺寸。
            viewport: 视口尺寸。
            width: 渲染宽度。

        Returns:
            折叠态为头行 + min(正文行数, cap + 1)（未超上限时即完整高度、无提示行）；
            展开态为完整高度 + 1 行收起提示。
        """
        full = super().get_content_height(container, viewport, width)
        self._full_height = full
        body = max(0, full - self._reserved)  # 扣除固定头行后的正文行数
        return self._reserved + (body + 1 if self._expanded else min(body, self._cap + 1))

    def on_resize(self, event: events.Resize) -> None:
        """宽度变化会改变折行，重新同步 -expandable 类。

        Args:
            event: 尺寸变化事件。
        """
        self.set_class(self._truncated or self._expanded, "-expandable")

    def toggle_expand(self) -> None:
        """截断 ↔ 完整（正文常驻，只切展开标记并按当前宽度重排）"""
        if not self._truncated and not self._expanded:
            return
        self._expanded = not self._expanded
        self.refresh(layout=True)

    def render_line(self, y: int) -> Strip:
        """渲染指定视觉行；折叠（已截断）/展开态的末行替换为提示行。

        Args:
            y: 视觉行号。

        Returns:
            该行的 Strip；正文行仍走基类渲染缓存。
        """
        if self._expanded and y == self._full_height:
            return self._hint_strip(_COLLAPSE_HINT)
        if not self._expanded and y == self._reserved + self._cap and self._truncated:
            hidden = self._full_height - self._reserved - self._cap
            return self._hint_strip(_TRUNCATED_HINT.format(hidden=hidden))
        return super().render_line(y)

    def _hint_strip(self, text: str) -> Strip:
        """构造提示行 Strip。

        Args:
            text: 提示文本。

        Returns:
            按当前宽度渲染的单行 Strip。
        """
        content = Content.from_rich_text(Text(text, style=_HINT_STYLE), console=self.app.console)
        options = RenderOptions(self._get_style, self.styles)
        return content.render_strips(
            self.size.width,
            1,
            self.visual_style,
            options,
        )[0]
