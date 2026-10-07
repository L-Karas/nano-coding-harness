"""_InlineConfirm：破坏性操作的原地确认——窗内提示行变红字，Enter 执行，Esc / 改选撤销并复原。"""
from core.tui.screens.base import _InlineConfirm


class _FakeHint:
    def __init__(self):
        self.value = "  hint"

    def render(self):
        return self.value

    def update(self, value):
        self.value = value


class _Dummy(_InlineConfirm):
    def __init__(self):
        self.hint = _FakeHint()
        self.ran = []

    def query_one(self, selector, cls=None):
        return self.hint


def test_enter_confirms_and_restores_hint():
    d = _Dummy()
    d._ask_confirm("Delete x?", lambda: d.ran.append("x"))
    assert d._pending is not None
    assert "Delete x?" in str(d.hint.value)
    assert "#f87171" in str(d.hint.value.spans[0].style)  # 红字提示（rich 富文本，渲染后转 rgb(248,113,113)）
    assert d._run_confirm() is True
    assert d.ran == ["x"] and d._pending is None
    assert d.hint.value == "  hint"  # 确认后提示行复原


def test_escape_and_highlight_change_cancel():
    d = _Dummy()
    d._ask_confirm("Delete y?", lambda: d.ran.append("y"))
    assert d._cancel_confirm() is True  # Esc
    assert d.ran == [] and d.hint.value == "  hint"
    assert d._run_confirm() is False  # 无待确认时 Enter 走原逻辑
    d._ask_confirm("Delete z?", lambda: d.ran.append("z"))
    d.on_option_list_option_highlighted(None)  # ↑/↓ 改选：自动撤销
    assert d.ran == [] and d._pending is None and d.hint.value == "  hint"
