"""主题与展示常量：标题 / 副标题、输入占位、Braille spinner 帧；样式表见 app.css。"""

from __future__ import annotations

DEFAULT_TITLE = "Nano Harness"
DEFAULT_SUBTITLE = "Enter your query and press Enter to send it. Type /exit or /quit to quit."
TERMINAL_TITLE = "nano-harness"  # 运行期终端窗口标题（OSC 2），退出时清空
_PLACEHOLDER = "Type your message, press Enter to send, and press Ctrl+J to start a new line."

# Braille spinner 帧；状态行 spin=True 与右栏进行中项轮播共用
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
