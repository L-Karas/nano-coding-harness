"""冒烟自检 · 页脚：cwd 行 + 占用条行（左占用条，右模型状态在右下角），占用条按 40%/80% 变色。"""
from __future__ import annotations

from textual.widgets import Static


async def run(app, pilot) -> None:
    import core.tui.footer as _mod
    from core.tui.utils import format_context_length

    class _FakeSession:
        def __init__(self, tokens: int):
            self._tokens = tokens

        def load_session_tokens(self) -> int:
            return self._tokens

    class _FakeModelState:
        def __init__(self, provider: str, model: str, level: str):
            self.current_provider = provider
            self.current_model = model
            self.current_thinking_level = level

    footer = app.query_one("#footer", Static)
    bar_widget = app.query_one("#token-bar", Static)
    empty_bar = "[" + "-" * 20 + "] -- / --"
    _manager, _ctx_len = app._manager, _mod.current_context_length
    _wd, _gb = _mod.working_directory, _mod.current_git_branch
    _model_state = _mod.current_model_state
    _has_client = True
    try:
        import core.client as _cm3
        _shared = _cm3.shared_model_client
    except Exception:
        _has_client = False

    def _rows() -> tuple[str, str]:
        return str(footer.render()), str(bar_widget.render())

    def _bar_styles() -> list[str]:
        return [str(span.style) for span in bar_widget.render().spans]

    def _has(*colors: str) -> bool:
        return any(c in style for style in _bar_styles() for c in colors)

    try:
        # 模型状态与占用条同行、在右下角；占用条钉成空灰条，右对齐可精确断言
        if _has_client:
            _mod.working_directory = lambda: "cwd"
            _mod.current_git_branch = lambda: ""
            _mod.current_context_length = lambda: 0
            _cm3.shared_model_client = lambda: _FakeModelState("p", "m", "max")
            app._refresh_footer()
            foot, bar_row = _rows()
            seg = "(p) m * max"
            assert foot == "cwd", f"页脚行应只保留 cwd: {foot!r}"
            expected = empty_bar + " " * (bar_widget.content_region.width - len(empty_bar) - len(seg)) + seg
            assert bar_row == expected, f"模型状态未右对齐到占用条行右下角: {bar_row!r}"
            tail = bar_widget.render().spans[-1]
            assert "#64748b" in str(tail.style) or "100,116,139" in str(tail.style), \
                f"模型段应为暗灰: {tail.style}"
            _cm3.shared_model_client = lambda: _FakeModelState("p", "m", "")
            app._refresh_footer()
            _, bar_row = _rows()
            assert " * " not in bar_row, "level 为空不应带 ' * ' 后缀"
            assert bar_row.rstrip().endswith("(p) m"), bar_row
            print("[smoke] footer row OK: cwd line + token bar line, '(provider) model * level' bottom-right")

        # 占用条：ASCII 条 + 占用百分比 / 上下文长度（K/M 一位小数）；阈值 40% / 80% 转绿→黄→红
        _mod.current_model_state = lambda: ("", "", "")  # 去掉模型段，行内容 = 纯占用条
        app._manager = _FakeSession(250_000)  # 25%：绿
        _mod.current_context_length = lambda: 1_000_000
        app._refresh_footer()
        assert _rows()[1] == "[#####---------------] 25.0% / 1.0M", f"占用条 M 单位/比例不符: {_rows()[1]!r}"
        assert _has("#4ade80", "74,222,128"), f"<40% 应为绿: {_bar_styles()}"

        app._manager = _FakeSession(51_200)  # 40.0% 阈值：黄
        _mod.current_context_length = lambda: 128_000
        app._refresh_footer()
        assert _rows()[1] == "[########------------] 40.0% / 128.0K", f"占用条渲染不符: {_rows()[1]!r}"
        assert _has("#facc15", "250,204,21"), f"40%–80% 应为黄: {_bar_styles()}"

        app._manager = _FakeSession(102_400)  # 80.0% 阈值：红
        app._refresh_footer()
        assert _rows()[1] == "[################----] 80.0% / 128.0K", f"占用条 80% 边界不符: {_rows()[1]!r}"
        assert _has("#f87171", "248,113,113"), f">=80% 应为红: {_bar_styles()}"

        app._manager = _FakeSession(1_234)
        _mod.current_context_length = lambda: 0
        app._refresh_footer()
        assert _rows()[1] == empty_bar, f"未配置模型时应显示灰条 -- / --: {_rows()[1]!r}"
        assert _has("#64748b", "100,116,139"), f"未配置模型条应为灰: {_bar_styles()}"
        print("[smoke] token context bar OK: ASCII bar + percent / context length, colors at 40%/80%")
    finally:
        _mod.working_directory, _mod.current_git_branch = _wd, _gb
        _mod.current_model_state = _model_state
        _mod.current_context_length = _ctx_len
        if _has_client:
            _cm3.shared_model_client = _shared
        app._manager = _manager
        app._refresh_footer()

    # 上下文长度格式化边界：K/M 一位小数，不足 1K 原样
    assert format_context_length(999) == "999"
    assert format_context_length(1_000) == "1.0K"
    assert format_context_length(128_000) == "128.0K"
    assert format_context_length(999_999) == "1000.0K"
    assert format_context_length(1_000_000) == "1.0M"
