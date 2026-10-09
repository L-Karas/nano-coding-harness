"""Skills 列表行：技能名后以 model provider 同款样式追加 [skill type] 来源标志。"""
from core.tui.screens.base import _entry_row, _model_row_text


def _span_style(row, text: str) -> str:
    span = next(s for s in row.spans if row.plain[s.start:s.end] == text)
    return str(span.style)


def test_entry_row_appends_skill_type_tag_with_provider_style():
    row = _entry_row("review", "Review code", "project")

    assert row.plain == "● review [project]\nReview code"
    assert _span_style(row, " [project]") == _span_style(_model_row_text("gpt", "openai"), " [openai]")


def test_entry_row_without_tag_is_unchanged():
    row = _entry_row("review", "Review code")

    assert row.plain == "● review\nReview code"
    assert " [" not in row.plain
