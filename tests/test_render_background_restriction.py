"""结构约束：`render_background_notification` 只允许后台任务结果回流使用。

后台任务结果之外的提示统一走 `notify`（Textual toast）；行为断言见
`tests/test_background_auto_loop.py`，这里用源码级断言防止调用点回流。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_tui_commands_do_not_render_background_cards():
    """指令 / 弹窗 / 回合错误提示一律走 notify，不再复用后台结果卡片。"""
    source = (ROOT / "core/tui/commands.py").read_text(encoding="utf-8")
    assert "render_background_notification" not in source


def test_agent_loop_renders_background_card_only_inside_auto_loop():
    """唯一调用点必须在 auto_loop 函数体内（收集后台结果后逐条上屏）。"""
    source = (ROOT / "core/agent_loop.py").read_text(encoding="utf-8")
    call = "interaction.render_background_notification("
    assert source.count(call) == 1
    auto_loop = source[source.index("def auto_loop("):]
    assert call in auto_loop
    assert auto_loop.index(call) < auto_loop.index("def start_agent_runtime("), \
        "后台结果卡片不能在 auto_loop 之外渲染"
