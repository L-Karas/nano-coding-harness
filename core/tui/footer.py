"""页脚两行：cwd (git 分支) + 上下文占用条 / 模型状态（右下角）。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from core.tui.utils import (
    current_context_length,
    current_git_branch,
    current_model_state,
    format_context_length,
    working_directory,
)

_TOKEN_BAR_CELLS = 20  # 占用条固定格数
_TEXT_STYLE = "#64748b"  # 暗灰（cwd 行 / 模型段 / 百分比段）


def _model_label() -> str:
    """'(provider) model * level'；未配置返回空串，无 level 时省略后缀。"""
    provider, model, level = current_model_state()
    if not provider or not model:
        return ""
    return f"({provider}) {model}" + (f" * {level}" if level else "")


def _token_bar_text(tokens: int, context_length: int) -> Text:
    """占用条：'[####----------------] 40.0% / 200.0K'；40% / 80% 阈值绿→黄→红；
    上下文长度未知时灰条 '[...] -- / --'；占比 >100% 条填满、百分比照实显示。"""
    if context_length <= 0:
        return Text("[" + "-" * _TOKEN_BAR_CELLS + "] -- / --", no_wrap=True, style=_TEXT_STYLE)
    percent = tokens / context_length * 100
    filled = min(_TOKEN_BAR_CELLS, int(percent * _TOKEN_BAR_CELLS / 100))
    color = "#4ade80" if percent < 40 else "#facc15" if percent < 80 else "#f87171"
    text = Text("[" + "#" * filled + "-" * (_TOKEN_BAR_CELLS - filled) + "]",
                no_wrap=True, style=color)
    text.append(f" {percent:.1f}% / {format_context_length(context_length)}", style=_TEXT_STYLE)
    return text


class _FooterBar(Vertical):
    """#footer（cwd 行）+ #token-bar（占用条 + 右下角模型状态，按实际行宽填隙对齐）。

    2s 自轮询（cwd / git 分支 / token 可能被其它进程或回合改变）；宽度变化由 App 触发重算。"""

    def compose(self) -> ComposeResult:
        yield Static("", id="footer", markup=False)
        yield Static("", id="token-bar", markup=False)

    def on_mount(self) -> None:
        self.refresh_data()
        self.call_after_refresh(self.refresh_data)  # 首帧宽度为 0，布局完成后再对齐一次
        self.set_interval(2.0, self.refresh_data)

    def refresh_data(self) -> None:
        cwd = working_directory()
        branch = current_git_branch()
        label = f"{cwd} ({branch})" if branch else cwd
        self.query_one("#footer", Static).update(Text(label, no_wrap=True, style=_TEXT_STYLE))

        bar = self.query_one("#token-bar", Static)
        text = Text(no_wrap=True)
        text.append_text(_token_bar_text(self._session_tokens(), current_context_length()))
        model_label = _model_label()
        if model_label:
            model = Text(model_label, no_wrap=True, style=_TEXT_STYLE)
            gap = bar.content_region.width - text.cell_len - model.cell_len
            if gap >= 0:  # 放不下则省略模型段
                text.append(" " * gap)
                text.append_text(model)
        bar.update(text)

    def _session_tokens(self) -> int:
        """当前会话 token 估算数；未接 SessionManager / 取值失败按 0。"""
        manager = getattr(self.app, "_manager", None)
        if manager is None:
            return 0
        try:
            return manager.load_session_tokens()
        except Exception:
            return 0
