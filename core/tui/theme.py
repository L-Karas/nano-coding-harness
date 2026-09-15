"""主题与展示常量：标题文案 / 加载动画帧 / Rich 标记安全解析。

从 ui_textual.py 拆出（widgets 不依赖本模块）。全局样式表拆至同目录 app.css
（原 _APP_CSS，ChatApp 经 CSS_PATH 加载），卡片配色随样式表维护。
"""

from __future__ import annotations

from rich.text import Text

DEFAULT_TITLE = "🤖 Nano-Harness Agent"
DEFAULT_SUBTITLE = "Enter your query and press Enter to send it. Type /exit or /quit to quit."
_PLACEHOLDER = "Type your message, press Enter to send, and press Ctrl+J to start a new line."

# 加载动画帧（标准 Braille spinner，10 帧）；状态行 spin=True 时在文本前轮播，Todos/Bg 处理中项同款轮播
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _markup(text: str, justify: str = "left") -> Text:
    """把 Rich 标记字符串安全地转成 Text（用户内容里出现残缺标记时降级为纯文本）"""
    try:
        return Text.from_markup(text, justify=justify)
    except Exception:
        return Text(text, justify=justify)

