"""页脚第三行显示当前审批模式；模式值按模式染色，模式变化后由页脚刷新即时反映。"""
import asyncio
from types import SimpleNamespace
from typing import get_args

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

import core.config
import core.tui.utils as utils
from core.config import ApprovalMode
from core.tui.footer import TOKEN_LABEL, approval_mode_text, token_bar_text
from core.tui.theme import APPROVAL_MODE_COLORS, SECONDARY_TEXT
from core.tui.ui_textual import ChatApp


def _fg(style) -> str:
    """样式（str / rich Style / textual Style）的前景色，统一成 '#' 十六进制。"""
    if isinstance(style, Style):
        parsed = style
    elif isinstance(style, str):
        parsed = Style.parse(style)
    else:
        parsed = style.rich_style  # textual.style.Style
    assert parsed.color is not None
    return "#%02x%02x%02x" % parsed.color.get_truecolor()


def _label_and_mode(text: Text) -> tuple[str, str, str]:
    """(标签色, 模式值文本, 模式值色)：Text 的基础样式是标签，append 进来的是唯一 span。"""
    assert len(text.spans) == 1
    span = text.spans[0]
    return _fg(text.style or ""), text.plain[span.start:span.end], _fg(span.style)


def _widget_label_and_mode(content) -> tuple[str, str, str]:
    """Static.render() 的 Content：首段 span 是拍平的基础样式（全行），末段是叠加的模式值。"""
    base, mode = content.spans[0], content.spans[-1]
    assert (base.start, base.end) == (0, len(content.plain))
    return _fg(base.style), content.plain[mode.start:mode.end], _fg(mode.style)


def test_current_approval_mode_reads_config(monkeypatch):
    monkeypatch.setattr(core.config, "CONFIGMANAGER",
                        SimpleNamespace(config=SimpleNamespace(approval_mode="Yolo")))
    assert utils.current_approval_mode() == "Yolo"


def test_current_approval_mode_falls_back_to_manual(monkeypatch):
    monkeypatch.setattr(core.config, "CONFIGMANAGER", SimpleNamespace())
    assert utils.current_approval_mode() == "Manual"


def test_footer_shows_approval_mode_and_refreshes(monkeypatch):
    config = SimpleNamespace(approval_mode="Manual")
    monkeypatch.setattr(core.config, "CONFIGMANAGER", SimpleNamespace(config=config))

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            row = app.query_one("#approval-mode", Static)
            assert str(row.render()) == "Approval Mode: Manual"
            assert _widget_label_and_mode(row.render()) == (SECONDARY_TEXT, "Manual", "#e5c890")
            config.approval_mode = "Yolo"
            app._refresh_footer()
            assert str(row.render()) == "Approval Mode: Yolo"
            assert _widget_label_and_mode(row.render()) == (SECONDARY_TEXT, "Yolo", "#e78284")

    asyncio.run(main())


def test_approval_mode_text_colors_mode_value_by_mode():
    """标签保持次级灰，仅模式值染色：Manual 黄 / Decision Model 与 LLM 绿 / Yolo 红。"""
    assert _label_and_mode(approval_mode_text("Manual")) == (SECONDARY_TEXT, "Manual", "#e5c890")
    assert _label_and_mode(approval_mode_text("Decision Model")) == (
        SECONDARY_TEXT, "Decision Model", "#a6d189")
    assert _label_and_mode(approval_mode_text("LLM")) == (SECONDARY_TEXT, "LLM", "#a6d189")
    assert _label_and_mode(approval_mode_text("Yolo")) == (SECONDARY_TEXT, "Yolo", "#e78284")
    assert approval_mode_text("Yolo").plain == "Approval Mode: Yolo"


def test_approval_mode_text_unmapped_mode_stays_secondary():
    assert _label_and_mode(approval_mode_text("Custom")) == (
        SECONDARY_TEXT, "Custom", SECONDARY_TEXT)


def test_approval_mode_colors_cover_every_declared_mode():
    assert set(APPROVAL_MODE_COLORS) == set(get_args(ApprovalMode))


def test_current_approval_mode_maps_unimplemented_to_manual(monkeypatch):
    monkeypatch.setattr(core.config, "CONFIGMANAGER",
                        SimpleNamespace(config=SimpleNamespace(approval_mode="LLM")))
    assert utils.current_approval_mode() == "Manual"


def testtoken_bar_text_has_context_usage_label():
    assert token_bar_text(0, 200_000).plain.startswith(f"{TOKEN_LABEL}: [")
    assert token_bar_text(0, 0).plain.startswith(f"{TOKEN_LABEL}: [")
