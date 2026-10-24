"""页脚第三行显示当前审批模式；模式值按模式染色，模式变化后由页脚刷新即时反映。"""
import asyncio
from types import SimpleNamespace
from typing import get_args

import pytest
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

import core.client
import core.config
import core.tui.footer as footer
import core.tui.utils as utils
from core.config import ApprovalMode
from core.tui.footer import (
    TOKEN_BAR_CELLS,
    TOKEN_LABEL,
    _DOTS_PER_CELL,
    _dot_cell,
    approval_mode_text,
    format_compact_threshold,
    token_bar_text,
)
from core.tui.theme import APPROVAL_MODE_COLORS, COMPACT_HINT, SECONDARY_TEXT
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
    monkeypatch.setattr(footer, "current_model_state", lambda: ("", "", ""))  # 固定为无模型段

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


def test_token_bar_text_appends_compact_threshold_hint():
    text = token_bar_text(0, 200_000, compact_threshold=160_000)
    assert text.plain == f"{TOKEN_LABEL}: [" + " " * 20 + "] 0.0% / 200.0K (compact at 160K)"
    hint = text.spans[-1]
    assert text.plain[hint.start:hint.end] == " (compact at 160K)"
    assert hint.style == COMPACT_HINT  # 阈值提示用区别于次级灰的青蓝色


def test_dot_cell_lights_dots_from_bottom_left():
    """逐点从底部点亮：0 点空白、8 点满格，前两个点铺满底行后向上。"""
    assert "".join(_dot_cell(n) for n in range(9)) == " ⡀⣀⣄⣤⣦⣶⣷⣿"


def test_token_bar_text_fills_braille_dots():
    """53% → 10 格满点 + 末格 5/8 高（⣦），其余格留白。"""
    text = token_bar_text(106_000, 200_000)
    assert text.plain == f"{TOKEN_LABEL}: [{'⣿' * 10}⣦{' ' * 9}] 53.0% / 200.0K"


def test_token_bar_text_resolves_sub_cell_granularity():
    """相邻 0.625% 落在一个新点上：50.0% 与 50.6% 的末格不同（旧条同为 10 格）。"""
    half = token_bar_text(100_000, 200_000)
    bumped = token_bar_text(101_200, 200_000)
    assert half.plain == f"{TOKEN_LABEL}: [{'⣿' * 10}{' ' * 10}] 50.0% / 200.0K"
    assert bumped.plain == f"{TOKEN_LABEL}: [{'⣿' * 10}⡀{' ' * 9}] 50.6% / 200.0K"
    assert half.plain != bumped.plain


def test_token_bar_text_saturates_at_full_capacity():
    full = token_bar_text(200_000, 200_000)
    assert full.plain == f"{TOKEN_LABEL}: [{'⣿' * 20}] 100.0% / 200.0K"
    assert token_bar_text(240_000, 200_000).plain == f"{TOKEN_LABEL}: [{'⣿' * 20}] 120.0% / 200.0K"


@pytest.mark.parametrize("dots", [0, 1, 7, 8, 79, 80, 159, 160, 161])
def test_token_bar_keeps_twenty_cells_at_every_level(dots):
    """每个点亮档位的条本体都占满 20 个终端格（盲文与空格字形宽度恒为 1）。

    换字形族（如 ▏▎▍）或字符宽度表变化时，这条断言先失败，而不是布局被挤歪才发现。"""
    tokens_per_dot = 200_000 // (TOKEN_BAR_CELLS * _DOTS_PER_CELL)  # 一档 0.625%
    bar = token_bar_text(dots * tokens_per_dot, 200_000).plain.split("[")[1].split("]")[0]
    assert cell_len(bar) == TOKEN_BAR_CELLS
    unknown = token_bar_text(0, 0).plain.split("[")[1].split("]")[0]
    assert cell_len(unknown) == TOKEN_BAR_CELLS


def test_token_bar_text_omits_compact_threshold_hint_without_threshold():
    assert "(compact" not in token_bar_text(0, 200_000).plain
    assert "(compact" not in token_bar_text(0, 200_000, compact_threshold=0).plain
    assert "(compact" not in token_bar_text(0, 0, compact_threshold=160_000).plain  # 无可用窗口时不提示


def test_token_bar_text_shows_no_compaction_hint_when_auto_compact_off():
    """自动压缩关闭时阈值不再自动触发，提示改说 '(auto-compact off)'。"""
    text = token_bar_text(0, 200_000, compact_threshold=160_000, auto_compact=False)
    assert text.plain.endswith("(auto-compact off)")
    assert "(compact at" not in text.plain
    hint = text.spans[-1]
    assert text.plain[hint.start:hint.end] == " (auto-compact off)"
    assert hint.style == COMPACT_HINT
    # 阈值是否已知不影响关闭状态提示；窗口未知时仍不提示
    assert token_bar_text(0, 200_000, auto_compact=False).plain.endswith("(auto-compact off)")
    assert "auto-compact" not in token_bar_text(
        0, 0, compact_threshold=160_000, auto_compact=False).plain


def test_format_compact_threshold_uses_k_or_m_units():
    assert format_compact_threshold(160_000) == "160K"
    assert format_compact_threshold(102_400) == "102.4K"
    assert format_compact_threshold(768) == "0.8K"
    assert format_compact_threshold(1_500_000) == "1.5M"
    assert format_compact_threshold(2_000_000) == "2.0M"


def test_footer_puts_model_state_on_approval_row(monkeypatch):
    """模型段移到审批模式行右端，占用条行不再包含模型。"""
    monkeypatch.setattr(footer, "current_model_state", lambda: ("p", "m", "max"))
    monkeypatch.setattr(footer, "current_approval_mode", lambda: "Manual")
    monkeypatch.setattr(footer, "current_context_length", lambda: 0)
    monkeypatch.setattr(footer, "current_compact_trigger_tokens", lambda: 0)

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            app._refresh_footer()
            bar = str(app.query_one("#token-bar", Static).render())
            row = app.query_one("#approval-mode", Static)
            assert bar.endswith("-- / --") and "(p) m * max" not in bar, bar
            rendered = str(row.render())
            assert rendered.startswith("Approval Mode: Manual"), rendered
            assert rendered.endswith("(p) m * max"), rendered
            assert len(rendered) == row.content_region.width, rendered

    asyncio.run(main())


def test_footer_token_bar_includes_compact_threshold(monkeypatch):
    monkeypatch.setattr(footer, "current_model_state", lambda: ("", "", ""))
    monkeypatch.setattr(footer, "current_context_length", lambda: 200_000)
    monkeypatch.setattr(footer, "current_compact_trigger_tokens", lambda: 160_000)
    monkeypatch.setattr(footer, "current_auto_compact", lambda: True)

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            app._refresh_footer()
            bar = str(app.query_one("#token-bar", Static).render())
            assert bar.endswith("0.0% / 200.0K (compact at 160K)"), bar

    asyncio.run(main())


def test_footer_shows_no_compaction_hint_when_auto_compact_is_off(monkeypatch):
    """关掉 auto_compact 后阈值不再自动触发，页脚把提示换成 '(auto-compact off)'。"""
    config = SimpleNamespace(auto_compact=False)
    monkeypatch.setattr(core.config, "CONFIGMANAGER", SimpleNamespace(config=config))
    monkeypatch.setattr(core.client, "shared_model_client",
                        lambda: SimpleNamespace(load_context_length=lambda: 200_000,
                                                compact_trigger_tokens=lambda: 160_000))
    monkeypatch.setattr(footer, "current_model_state", lambda: ("", "", ""))

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            app._refresh_footer()
            bar = str(app.query_one("#token-bar", Static).render())
            assert bar.endswith("0.0% / 200.0K (auto-compact off)"), bar
            config.auto_compact = True
            app._refresh_footer()
            bar = str(app.query_one("#token-bar", Static).render())
            assert bar.endswith("0.0% / 200.0K (compact at 160K)"), bar

    asyncio.run(main())
