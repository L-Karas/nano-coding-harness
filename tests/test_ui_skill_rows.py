"""Skills 列表行：技能名后以专用来源色追加 [skill type] 来源标志（与 model provider 标记区分）。"""
from core.tui.screens.base import _entry_row, _model_row_text


def _span_style(row, text: str) -> str:
    span = next(s for s in row.spans if row.plain[s.start:s.end] == text)
    return str(span.style)


def test_entry_row_appends_skill_type_tag_with_dedicated_style():
    row = _entry_row("review", "Review code", "project")

    assert row.plain == "● review [project]\nReview code"
    assert _span_style(row, " [project]") == "#a5adce"
    # 模型行的 [Provider] 样式保持原样，不受技能来源标色影响
    assert _span_style(row, " [project]") != _span_style(_model_row_text("gpt", "openai"), " [openai]")


def test_entry_row_without_tag_is_unchanged():
    row = _entry_row("review", "Review code")

    assert row.plain == "● review\nReview code"
    assert " [" not in row.plain
