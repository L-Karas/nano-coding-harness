"""冒烟自检 · 页脚：cwd 行 + 占用条行（占比 + 压缩阈值提示）+ 审批模式行（右端模型状态），占用条按 40%/80% 变色。"""
from __future__ import annotations

from textual.widgets import Static


async def run(app, pilot) -> None:
    import core.tui.footer as _mod
    from core.tui.footer import format_compact_threshold
    from core.tui.utils import format_context_length

    class _FakeSession:
        def __init__(self, tokens: int):
            self._tokens = tokens

        def load_session_tokens(self) -> int:
            return self._tokens

    class _FakeModelState:
        def __init__(
            self,
            provider: str,
            model: str,
            level: str,
        ):
            self.current_provider = provider
            self.current_model = model
            self.current_thinking_level = level

    footer = app.query_one("#footer", Static)
    bar_widget = app.query_one("#token-bar", Static)
    approval_widget = app.query_one("#approval-mode", Static)
    empty_bar = f"{_mod.TOKEN_LABEL}: [" + "-" * _mod.TOKEN_BAR_CELLS + "] -- / --"
    prefix = f"{_mod.TOKEN_LABEL}: ["
    _manager, _ctx_len = app._manager, _mod.current_context_length
    _wd, _gb = _mod.working_directory, _mod.current_git_branch
    _model_state = _mod.current_model_state
    _approval_mode = _mod.current_approval_mode
    _compact = _mod.current_compact_trigger_tokens
    _auto_compact = _mod.current_auto_compact
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
        # 模型状态与审批模式同行、在右下角；占用条钉成空灰条，右对齐可精确断言
        if _has_client:
            _mod.working_directory = lambda: "cwd"
            _mod.current_git_branch = lambda: ""
            _mod.current_context_length = lambda: 0
            _mod.current_compact_trigger_tokens = lambda: 0
            _mod.current_auto_compact = lambda: True
            _mod.current_approval_mode = lambda: "Manual"
            _cm3.shared_model_client = lambda: _FakeModelState("p", "m", "max")
            app._refresh_footer()
            foot, bar_row = _rows()
            seg = "(p) m * max"
            assert foot == "cwd", f"页脚行应只保留 cwd: {foot!r}"
            assert bar_row == empty_bar, f"占用条行应只保留占用条（模型已移到审批模式行）: {bar_row!r}"
            approval = approval_widget.render()
            label = "Approval Mode: Manual"
            gap = approval_widget.content_region.width - len(label) - len(seg)
            expected = label + " " * gap + seg
            assert str(approval) == expected, f"模型状态未右对齐到审批模式行: {str(approval)!r}"
            tail = approval.spans[-1]
            assert "#94a3b8" in str(tail.style) or "148,163,184" in str(tail.style), \
                f"模型段应为次级灰: {tail.style}"
            _cm3.shared_model_client = lambda: _FakeModelState("p", "m", "")
            app._refresh_footer()
            _, bar_row = _rows()
            approval_row = str(approval_widget.render())
            assert " * " not in approval_row, "level 为空不应带 ' * ' 后缀"
            assert approval_row.rstrip().endswith("(p) m"), approval_row
            assert "(p) m" not in bar_row, "模型段不应留在占用条行"
            print("[smoke] footer row OK: cwd line + token bar line, '(provider) model * level' bottom-right of approval row")

        # 审批模式：占用条下方单独一行，随配置实时刷新
        _mod.current_model_state = lambda: ("", "", "")  # 去掉模型段
        _mod.current_approval_mode = lambda: "Yolo"
        app._refresh_footer()
        assert str(approval_widget.render()) == "Approval Mode: Yolo", \
            f"审批模式行不符: {approval_widget.render()!r}"
        print("[smoke] approval mode row OK: 'Approval Mode: <mode>' below token bar")

        # 占用条：盲文点阵（20 格 × 8 点，末格按点亮点数呈现部分填充）+ 占用百分比 /
        # 上下文长度（K/M 一位小数）+ 压缩阈值提示（K/M）；阈值 40% / 80% 转绿→黄→红
        _mod.current_model_state = lambda: ("", "", "")  # 去掉模型段，行内容 = 纯占用条
        app._manager = _FakeSession(250_000)  # 25%：5 格满点，绿
        _mod.current_context_length = lambda: 1_000_000
        _mod.current_compact_trigger_tokens = lambda: 800_000
        _mod.current_auto_compact = lambda: True
        app._refresh_footer()
        assert _rows()[1] == f"{prefix}{'⣿' * 5}{' ' * 15}] 25.0% / 1.0M (compact at 800K)", \
            f"占用条 M 单位/压缩阈值不符: {_rows()[1]!r}"
        assert _has("#4ade80", "74,222,128"), f"<40% 应为绿: {_bar_styles()}"
        assert _has("#7dd3fc", "125,211,252"), f"压缩阈值提示应使用青色: {_bar_styles()}"

        app._manager = _FakeSession(256_000)  # 25.6%：末格多亮 1 个点（一档 0.625%）
        app._refresh_footer()
        assert _rows()[1] == f"{prefix}{'⣿' * 5}⡀{' ' * 14}] 25.6% / 1.0M (compact at 800K)", \
            f"末格应显示格内精度: {_rows()[1]!r}"

        app._manager = _FakeSession(51_200)  # 40.0% 阈值：黄
        _mod.current_context_length = lambda: 128_000
        _mod.current_compact_trigger_tokens = lambda: 96_000
        app._refresh_footer()
        assert _rows()[1] == f"{prefix}{'⣿' * 8}{' ' * 12}] 40.0% / 128.0K (compact at 96K)", \
            f"占用条渲染不符: {_rows()[1]!r}"
        assert _has("#facc15", "250,204,21"), f"40%–80% 应为黄: {_bar_styles()}"

        app._manager = _FakeSession(102_400)  # 80.0% 阈值：红
        app._refresh_footer()
        assert _rows()[1] == f"{prefix}{'⣿' * 16}{' ' * 4}] 80.0% / 128.0K (compact at 96K)", \
            f"占用条 80% 边界不符: {_rows()[1]!r}"
        assert _has("#f87171", "248,113,113"), f">=80% 应为红: {_bar_styles()}"

        _mod.current_auto_compact = lambda: False  # 关闭自动压缩：提示换为无压缩
        app._refresh_footer()
        assert _rows()[1] == f"{prefix}{'⣿' * 16}{' ' * 4}] 80.0% / 128.0K (auto-compact off)", \
            f"auto_compact 关闭时提示不符: {_rows()[1]!r}"
        _mod.current_auto_compact = lambda: True

        app._manager = _FakeSession(1_234)
        _mod.current_context_length = lambda: 0
        _mod.current_compact_trigger_tokens = lambda: 0
        app._refresh_footer()
        assert _rows()[1] == empty_bar, f"未配置模型时应显示灰条 -- / --: {_rows()[1]!r}"
        assert _has("#94a3b8", "148,163,184"), f"未配置模型条应为灰: {_bar_styles()}"
        print("[smoke] token context bar OK: braille dot bar + percent / context length + compact threshold (or 'auto-compact off'), colors at 40%/80%")
    finally:
        _mod.working_directory, _mod.current_git_branch = _wd, _gb
        _mod.current_model_state = _model_state
        _mod.current_context_length = _ctx_len
        _mod.current_approval_mode = _approval_mode
        _mod.current_compact_trigger_tokens = _compact
        _mod.current_auto_compact = _auto_compact
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
    # 压缩阈值提示格式化：K 单位，整 K 不带小数
    assert format_compact_threshold(800_000) == "800K"
    assert format_compact_threshold(96_000) == "96K"
    assert format_compact_threshold(102_400) == "102.4K"
