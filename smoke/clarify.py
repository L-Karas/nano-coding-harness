"""冒烟自检 · clarify 询问：单选 / 多选（Space 勾选 ◉/◯）/ Other 输入 / Esc 取消。"""
from __future__ import annotations

from concurrent.futures import Future

from smoke._util import settle
from core.tui.widgets import _ClarifyList


async def run(app, pilot) -> None:

    # 单选：↑/↓ 移动高亮，Enter 选中高亮项作答
    future: Future = Future()
    app._begin_clarify(["Use SQLite?", "Use Postgres?"], False, future)
    await settle(pilot)
    ol = app.query_one("#clarify-list", _ClarifyList)
    assert ol.styles.display != "none" and ol.has_focus, "clarify 列表应显示并持焦"
    assert ol.option_count == 3 and ol.get_option_at_index(2).id == "other", "末尾应为 Other"
    assert app._prompt().disabled, "作答前输入条应禁用"
    await pilot.press("down", "enter")
    assert future.result() == "Use Postgres?", future.result()
    assert ol.styles.display == "none" and not app._clarify_pending, "作答后应收起列表"
    print("[smoke] clarify single-select OK")

    # 多选：Space 切换勾选（◉/◯ 标记），Enter 提交全部勾选项
    future = Future()
    app._begin_clarify(["A", "B", "C"], True, future)
    await settle(pilot)
    ol = app.query_one("#clarify-list", _ClarifyList)
    assert str(ol.get_option_at_index(0).prompt).startswith("◯ A"), "多选应带未勾选标记"
    await pilot.press("space", "down", "space")  # 勾 A、B
    assert str(ol.get_option_at_index(0).prompt).startswith("◉ A"), "Space 未切换勾选标记"
    await pilot.press("enter")
    assert future.result() == "- A\n- B", future.result()
    assert ol.styles.display == "none"
    print("[smoke] clarify multi-select OK (space toggles)")

    # Other：Enter 激活后输入条以提示语占位，提交文本作为答案
    future = Future()
    app._begin_clarify(["Yes", "No"], False, future)
    await settle(pilot)
    ol = app.query_one("#clarify-list", _ClarifyList)
    await pilot.press("down", "down")  # 高亮移到末尾 Other
    assert ol.highlighted == 2
    await pilot.press("enter")
    await settle(pilot)
    assert app._clarify_other and not app._prompt().disabled and app._prompt().has_focus
    assert app._prompt().placeholder == "Type your own answer…", app._prompt().placeholder
    app._prompt().text = "custom reply"
    await pilot.press("enter")
    assert future.result() == "custom reply", future.result()
    print("[smoke] clarify Other input OK (placeholder + typed answer)")

    # 空选项列表只剩 Other；Esc 取消
    future = Future()
    app._begin_clarify([], False, future)
    await settle(pilot)
    ol = app.query_one("#clarify-list", _ClarifyList)
    assert ol.option_count == 1 and ol.get_option_at_index(0).id == "other", "空列表应只剩 Other"
    await pilot.press("escape")
    assert future.result() == "[User cancelled]", future.result()
    assert ol.styles.display == "none"
    print("[smoke] clarify Esc cancel OK")

    # 端到端：非 App 线程经渲染桥调工具入口 run_clarify，UI 作答后返回
    import threading

    result: dict = {}

    def _worker() -> None:
        from core.tools.base_tools.clarify import run_clarify
        result["answer"] = run_clarify(["Bridge [A]?", "Bridge [B]?"], False)  # 含 markup 字符：不得被解析

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    for _ in range(100):
        await pilot.pause(0.05)
        if app._clarify_pending:
            break
    assert app._clarify_pending, "run_clarify 未经渲染桥拉起澄清列表"
    await pilot.press("enter")
    thread.join(timeout=5)
    assert result.get("answer") == "Bridge [A]?", result
    print("[smoke] clarify tool entry OK (ask_clarify bridge from worker thread)")
