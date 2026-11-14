"""nano-harness 的 Textual 全屏界面：包级 Interface 只有 run 与 ChatApp。

其余模块（render / blocks / board / ask / ask_panel / parts / screens …）都是实现；
外部只经这两个符号接入。
惰性导入：`import core.tui.blocks` 不拉起 Textual。
"""

from __future__ import annotations

from typing import Any

__all__ = ["run", "ChatApp"]


def __getattr__(name: str) -> Any:
    """按需解析包级符号。

    Args:
        name: 属性名。

    Returns:
        `run` / `ChatApp` 的实现（经 core.tui.ui_textual）。

    Raises:
        AttributeError: 其它属性名。
    """
    if name in __all__:
        from core.tui import ui_textual

        return getattr(ui_textual, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
