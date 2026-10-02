"""主题与展示常量：默认标题 / 副标题、输入框占位文案、加载动画帧（Braille spinner）。
全局样式表在同目录 app.css（ChatApp 经 CSS_PATH 加载）。"""

from __future__ import annotations

DEFAULT_TITLE = "Nano Harness"
DEFAULT_SUBTITLE = "Enter your query and press Enter to send it. Type /exit or /quit to quit."
_PLACEHOLDER = "Type your message, press Enter to send, and press Ctrl+J to start a new line."

# 加载动画帧（标准 Braille spinner，10 帧）；状态行 spin=True 时在文本前轮播，Todos/Bg 处理中项同款轮播
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
