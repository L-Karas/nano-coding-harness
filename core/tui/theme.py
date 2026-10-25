"""主题与展示常量：标题 / 副标题、输入占位、Braille spinner 帧；样式表见 app.css。"""

from __future__ import annotations

DEFAULT_TITLE = "Nano Harness"
DEFAULT_SUBTITLE = "Enter your query and press Enter to send it. Type /exit or /quit to quit."
TERMINAL_TITLE = "nano-harness"  # 运行期终端窗口标题（OSC 2），退出时清空
SECONDARY_TEXT = "#94a3b8"  # 次级文字色：说明 / 未配置 / 占位提示等
COMPACT_HINT = "#7dd3fc"  # 页脚压缩阈值提示色（青蓝，区别于次级灰）
PLACEHOLDER = "Type a message… Enter to send · Ctrl+J for newline"

# 状态字形 / 配色：/extensions 启用/禁用、/mcp connected/failed 共用
ACTIVE_GLYPH = "●"
INACTIVE_GLYPH = "○"
ACTIVE_STYLE = "bold #4ade80"
INACTIVE_STYLE = "#f87171"

# 页脚审批模式值配色（按危险度分档）：Manual 黄 / Decision Model·LLM 绿 / Yolo 红；表外模式回落次级灰
APPROVAL_MODE_COLORS = {
    "Manual": "#e5c890",
    "Decision Model": "#a6d189",
    "LLM": "#a6d189",
    "Yolo": "#e78284",
}

# Braille spinner 帧；状态行 spin=True 与右栏进行中项轮播共用
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
