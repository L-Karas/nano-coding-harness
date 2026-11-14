"""状态行部件：文本 + 可选 Rich Spinner 轮播（Textual auto_refresh 驱动，非动画态停表）。"""

from __future__ import annotations

from typing import Any

from rich.spinner import Spinner
from rich.text import Text
from textual.widgets import Static


class _StatusLine(Static):
    """状态行：文本 + 可选 Rich Spinner 轮播（Textual auto_refresh 驱动，非动画态停表）。"""

    def __init__(self, **kwargs: Any) -> None:
        """初始化状态行。

        Args:
            kwargs: 透传给 Static 的参数。
        """
        super().__init__("", **kwargs)
        self._text = ""
        self._spinner: Spinner | None = None

    def set_text(self, text: str, spin: bool = False) -> None:
        """设置状态文本（纯文本渲染）；spin 为真时用 Rich Spinner 轮播并自动重绘。

        Args:
            text: 状态文本。
            spin: 是否显示 spinner 动画。
        """
        if text == self._text and spin == (self._spinner is not None):
            return  # 同一状态重复设置：不重建 Spinner（避免动画相位抖动）
        self._text = text
        self._spinner = Spinner("dots", text=Text(text)) if spin else None
        if self._spinner is not None:
            if self.auto_refresh is None:
                self.auto_refresh = self._spinner.interval / 1000
            self.update(self._spinner)
        else:
            self.auto_refresh = None
            self.update(Text(text))

    def swap(self, text: str, spin: bool = True) -> tuple[str, bool]:
        """切换状态文本。

        Args:
            text: 新状态文本。
            spin: 是否显示 spinner 动画。

        Returns:
            旧状态 (文本, 是否动画)，供调用方恢复。
        """
        previous = (self._text, self._spinner is not None)
        self.set_text(text, spin=spin)
        return previous
