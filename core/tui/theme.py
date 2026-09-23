"""主题与展示常量：标题文案 / 加载动画帧。

从 ui_textual.py 拆出（widgets 不依赖本模块）。全局样式表拆至同目录 app.css
（原 _APP_CSS，ChatApp 经 CSS_PATH 加载），卡片配色随样式表维护。
"""

from __future__ import annotations

DEFAULT_TITLE = "Nano Harness"
DEFAULT_SUBTITLE = "Enter your query and press Enter to send it. Type /exit or /quit to quit."
_PLACEHOLDER = "Type your message, press Enter to send, and press Ctrl+J to start a new line."

# 加载动画帧（标准 Braille spinner，10 帧）；状态行 spin=True 时在文本前轮播，Todos/Bg 处理中项同款轮播
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
