"""页脚三行：cwd (git 分支) + 上下文占用条（含压缩阈值提示）+ 审批模式 / 模型状态（右下角）。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from core.tui.theme import APPROVAL_MODE_COLORS, COMPACT_HINT, SECONDARY_TEXT
from core.tui.utils import (
    current_approval_mode,
    current_auto_compact,
    current_compact_trigger_tokens,
    current_context_length,
    current_git_branch,
    current_model_state,
    format_context_length,
    working_directory,
)

TOKEN_BAR_CELLS = 20  # 占用条固定格数
_BRAILLE_BASE = 0x2800
_BAR_EMPTY = " "  # 空槽留白（盲文空字形 U+2800 与空格同宽，空格更稳妥）
# 点亮顺序：从下往上逐行，每行先左点后右点，对应盲文点号 (7,8) (3,6) (2,5) (1,4)
_DOT_ROWS = ((0x40, 0x80), (0x04, 0x20), (0x02, 0x10), (0x01, 0x08))
_DOTS_PER_CELL = len(_DOT_ROWS) * 2  # 每格 8 个盲文点（2 列 × 4 行）
TOKEN_LABEL = "Context Usage"  # 占用条前置标签
APPROVAL_LABEL = "Approval Mode"  # 审批模式行前置标签
_TEXT_STYLE = SECONDARY_TEXT  # 次级灰（cwd 行 / 模型段 / 标签 / 百分比段）


def approval_mode_text(mode: str) -> Text:
    """'Approval Mode: <mode>'：标签次级灰，仅模式值按模式染色（表外模式也走次级灰）。"""
    text = Text(f"{APPROVAL_LABEL}: ", no_wrap=True, style=_TEXT_STYLE)
    text.append(mode, style=APPROVAL_MODE_COLORS.get(mode, _TEXT_STYLE))
    return text


def _model_label() -> str:
    """'(provider) model * level'；未配置返回空串，无 level 时省略后缀。"""
    provider, model, level = current_model_state()
    if not provider or not model:
        return ""
    return f"({provider}) {model}" + (f" * {level}" if level else "")


def format_compact_threshold(tokens: int) -> str:
    """压缩阈值提示：K/M 单位，整单位省略小数（160000 → '160K'，1500000 → '1.5M'）。"""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens % 1000 == 0:
        return f"{tokens // 1000}K"
    return f"{tokens / 1000:.1f}K"


def _dot_cell(dots: int) -> str:
    """0–8 个点对应的盲文字形（≤0 为空格，≥8 为满格）：逐行从底部点起，每行先左点后右点。"""
    if dots <= 0:
        return _BAR_EMPTY
    bits = 0
    for row, (left, right) in enumerate(_DOT_ROWS):
        if dots > row * 2:
            bits |= left
        if dots > row * 2 + 1:
            bits |= right
    return chr(_BRAILLE_BASE + bits)


def token_bar_text(tokens: int, context_length: int, *, compact_threshold: int = 0,
                   auto_compact: bool = True) -> Text:
    """占用条：'Context Usage: [⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦         ] 53.0% / 200.0K (compact at 160K)'；
    盲文点阵 20 格 × 8 点，末格按点亮点数呈现部分填充（一档 0.625%）；
    40% / 80% 阈值绿→黄→红；末尾括号提示压缩阈值（K/M 单位），
    auto_compact 关闭时提示 '(auto-compact off)'（阈值不再自动触发）；
    阈值为 0（未知）时不提示；上下文长度未知时灰条 'Context Usage: [...] -- / --'（无提示）；
    占比 >100% 条填满、百分比照实显示。"""
    text = Text(f"{TOKEN_LABEL}: ", no_wrap=True, style=_TEXT_STYLE)
    if context_length <= 0:
        text.append("[" + "-" * TOKEN_BAR_CELLS + "] -- / --", style=_TEXT_STYLE)
        return text
    percent = tokens / context_length * 100
    total_dots = TOKEN_BAR_CELLS * _DOTS_PER_CELL
    dots = min(total_dots, max(0, round(percent * total_dots / 100)))
    # 每格取「剩余点数」：≥8 满格、1–7 为末格的部分点、≤0 为空槽
    bar = "".join(_dot_cell(dots - i * _DOTS_PER_CELL) for i in range(TOKEN_BAR_CELLS))
    color = "#4ade80" if percent < 40 else "#facc15" if percent < 80 else "#f87171"
    text.append(f"[{bar}]", style=color)
    text.append(f" {percent:.1f}% / {format_context_length(context_length)}", style=_TEXT_STYLE)
    if not auto_compact:
        text.append(" (auto-compact off)", style=COMPACT_HINT)
    elif compact_threshold > 0:
        text.append(f" (compact at {format_compact_threshold(compact_threshold)})",
                    style=COMPACT_HINT)
    return text


class _FooterBar(Vertical):
    """#footer（cwd 行）+ #token-bar（占用条 + 压缩阈值）+ #approval-mode（当前审批模式
    + 右下角模型状态，按实际行宽填隙对齐）。

    2s 自轮询（cwd / git 分支 / token / 阈值 / 审批模式可能被其它进程或回合改变）；宽度变化由 App 触发重算。"""

    def compose(self) -> ComposeResult:
        yield Static("", id="footer", markup=False)
        yield Static("", id="token-bar", markup=False)
        yield Static("", id="approval-mode", markup=False)

    def on_mount(self) -> None:
        self.refresh_data()
        self.call_after_refresh(self.refresh_data)  # 首帧宽度为 0，布局完成后再对齐一次
        self.set_interval(2.0, self.refresh_data)

    def refresh_data(self) -> None:
        cwd = working_directory()
        branch = current_git_branch()
        label = f"{cwd} ({branch})" if branch else cwd
        self.query_one("#footer", Static).update(Text(label, no_wrap=True, style=_TEXT_STYLE))

        self.query_one("#token-bar", Static).update(token_bar_text(
            self._session_tokens(), current_context_length(),
            compact_threshold=current_compact_trigger_tokens(),
            auto_compact=current_auto_compact()))

        row = self.query_one("#approval-mode", Static)
        text = approval_mode_text(current_approval_mode())
        model_label = _model_label()
        if model_label:
            model = Text(model_label, no_wrap=True, style=_TEXT_STYLE)
            gap = row.content_region.width - text.cell_len - model.cell_len
            if gap >= 0:  # 放不下则省略模型段
                text.append(" " * gap)
                text.append_text(model)
        row.update(text)

    def _session_tokens(self) -> int:
        """当前会话 token 估算数；未接 SessionManager / 取值失败按 0。"""
        manager = getattr(self.app, "_manager", None)
        if manager is None:
            return 0
        try:
            return manager.load_session_tokens()
        except Exception:
            return 0
