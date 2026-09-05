"""主题与展示常量：卡片配色 / 加载动画帧 / 全局 CSS / Rich 标记安全解析。

从 ui_textual.py 拆出（ui_textual 组装整体样式、widgets 不依赖本模块）。
"""

from __future__ import annotations

from rich.text import Text

DEFAULT_TITLE = "🤖 Nano Coding Harness Agent"
DEFAULT_SUBTITLE = "输入问题后回车发送 · /new 新会话 · /sessions 切换 · /clear 清屏 · /exit 退出"
_PLACEHOLDER = "输入消息，Enter 发送 · Ctrl+J 换行（/exit 退出）"

# 每类卡片主题：左边实线颜色 / 背景色（对齐原 ui.py 配色；标题行已去除，靠色条/底色区分消息类型）
_CARD_THEME = {
    "user":      {"bar": "#6b7280", "bg": "#374151"},
    "tool":      {"bar": "#f59e0b", "bg": "#261f0d"},
    "result":    {"bar": "#10b981", "bg": "#11221b"},
    "assistant": {"bar": "#c084fc", "bg": "#1e1b2e"},
    "notice":    {"bar": "#22d3ee", "bg": "#0f172a"},
    "sessions":  {"bar": "#38bdf8", "bg": "#0c1a2e"},
    "perm":      {"bar": "#f59e0b", "bg": "#3b2a10"},
}

# 加载动画帧（标准 Braille spinner，10 帧）；状态行 spin=True 时在文本前轮播
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _markup(text: str, justify: str = "left") -> Text:
    """把 Rich 标记字符串安全地转成 Text（用户内容里出现残缺标记时降级为纯文本）"""
    try:
        return Text.from_markup(text, justify=justify)
    except Exception:
        return Text(text, justify=justify)


def _theme_css() -> str:
    return "\n".join(
        f".card.{kind} {{ background: {t['bg']}; border-left: heavy {t['bar']}; }}"
        for kind, t in _CARD_THEME.items()
    )


_APP_CSS = f"""
App {{ background: ansi_default; }}  /* DOM 根背景用终端默认色（SGR 49），透明区域透出 terminal 原生背景；否则 Textual 会垫 #121212 主题色 */

Screen {{ background: transparent; }}  /* 背景透出终端原生底色，与 terminal 一致 */

#titlebar {{ width: 1fr; height: auto; background: #0c1a2e; padding: 0 2; }}
#titlebar > * {{ width: 1fr; }}
#chat {{ width: 1fr; height: 1fr; background: transparent; padding: 1 0; scrollbar-size-vertical: 1; scrollbar-background: transparent; scrollbar-background-hover: transparent; scrollbar-background-active: transparent; scrollbar-color: #475569; scrollbar-color-hover: #94a3b8; scrollbar-color-active: #38bdf8; }}  /* 纵向滑块 1 列宽：拖动拇指 / 点击轨道翻页 / 滚轮浏览消息历史；仅内容超出视口时出现并占 1 列（画板随之窄 1 列，无溢出时仍占满全宽）；颜色与卡片边框一致，hover/拖动高亮 */
#dock {{ dock: bottom; width: 1fr; height: auto; background: transparent; }}
#status {{ width: 1fr; height: 1; background: transparent; color: #a5b4fc; padding: 0 2; }}
#cmd-suggest, #file-suggest {{ display: none; width: 1fr; height: auto; margin: 0 2; background: transparent; border: solid #475569; }}
#cmd-suggest {{ max-height: 6; }}
#file-suggest {{ max-height: 10; }}
#cmd-suggest ListItem, #file-suggest ListItem {{ height: 1; padding: 0 1; }}
#inputbar {{ width: 1fr; height: auto; background: transparent; border-top: heavy #475569; border-bottom: heavy #475569; }}
#prompt-mark {{ width: auto; color: #7dd3fc; padding: 0 0 0 2; }}
#prompt {{ width: 1fr; height: auto; max-height: 10; background: transparent; border: none; color: #f8fafc; padding: 0 1 0 1; }}  /* height:auto：空值 1 行；内容超出终端宽度自动换行、多行伸缩；超 10 行时内部滚动 */
#footer {{ width: 1fr; height: 1; background: transparent; color: #64748b; padding: 0 2; }}  /* 输入条正下方的信息行：当前工作目录 (git 分支)，文本靠最左 */

.card {{ width: 1fr; height: auto; margin: 0 0 1 0; padding: 1 2; }}  /* 关键：height:auto 才能随内容量伸缩（Vertical 默认 height:1fr 会按视口均分并裁掉超出内容）；padding 1 2 让内容与边界留白；无左右 margin 保证卡片与终端同宽 */
.card > * {{ width: 1fr; }}
.card .card-body {{ text-align: left; }}
.card.assistant > Markdown {{ padding: 0; }}  /* Markdown 自带 padding:0 2，去掉使内容与其它卡片对齐 */
{_theme_css()}

.picker {{
    width: 76%; height: 80%;
    padding: 1 2;
    background: transparent; border: round #38bdf8;  /* 背景透出终端原生底色（与聊天区一致）；会话列表本身透明，行高亮仍可见 */
}}

ModalScreen {{ background: transparent; }}  /* 去掉默认的主题色半透明遮罩，整屏保持 terminal 底色 */
.picker-title {{ width: 1fr; text-align: center; color: #38bdf8; padding: 0 0 1 0; }}
.picker-hint {{ width: 1fr; color: #64748b; padding: 1 0 0 0; }}
#sess-list {{ border: none; background: transparent; height: 1fr; margin-top: 1; }}
"""
