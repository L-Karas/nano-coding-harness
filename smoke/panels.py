"""冒烟自检 · 右栏信息分区：Todos / Background Tasks / Subagents 实时同步、独立折叠、条目展开。"""
from __future__ import annotations

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from core.tui.theme import _SPINNER_FRAMES


async def run(app, pilot) -> None:

    # 右栏信息分区：Todos / Background Tasks / Subagents 折叠列表（Skills 已迁往 /skills
    # 弹窗，不再显示在右栏）。默认展开并同步 RuntimeState 快照（ADR-0004，1s 轮询）；
    # 标题（▼/▶ + 计数）嵌在上边框，点边框独立折叠/展开（列表本体隐藏，边框标题保留）
    from core.runtime_state import RUNTIME_STATE, TodoEntry
    from core.tools import ToolResult
    todos_section = app.query_one("#todos-section", Vertical)
    bg_section = app.query_one("#bg-section", Vertical)
    sub_section = app.query_one("#subagents-section", Vertical)
    assert str(todos_section.border_title).startswith("▼ Todos"), "Todos 分区应默认展开"
    assert str(bg_section.border_title).startswith("▼ Background Tasks"), "bg 分区应默认展开"
    assert str(sub_section.border_title).startswith("▼ Subagents"), "Subagents 分区应默认展开"
    assert not list(app.query("#skills-head")) and not list(app.query("#skills-section")), \
        "Skills 不应再显示在右栏（已迁往 /skills 弹窗）"
    assert todos_section.region.y < bg_section.region.y < sub_section.region.y, \
        "分区顺序应为 todos / bg / subagents"
    RUNTIME_STATE.set_todos([TodoEntry("add right-panel smoke checks", "in_progress"),
                             TodoEntry("hook up the real agent", "completed")])
    RUNTIME_STATE.register_background(
        "bg-7777", "terminal(command='pip install textual', cwd='/very/long/remote/path')")
    RUNTIME_STATE.register_subagent("sa-0001", "summarize the asyncio doc")
    await pilot.pause(1.3)  # 覆盖 ≥1 次 1s 轮询

    def _rows_text(sel: str) -> str:
        """条目行文本拼接（每行一条 .info-row Static）"""
        return " | ".join(str(w.render()) for w in app.query(sel))

    todos_str = _rows_text("#todos-list .info-row")
    assert "add right-panel smoke checks" in todos_str and "hook up the real agent" in todos_str, todos_str
    assert any(g in todos_str for g in _SPINNER_FRAMES), f"in_progress 项应有轮播字形: {todos_str}"
    await pilot.pause(0.5)  # 覆盖 ≥2 次 0.2s 动画 tick
    todos_animated = _rows_text("#todos-list .info-row")
    assert todos_animated != todos_str, "in_progress todo 应呈动态加载效果（帧未推进）"
    bg_str = _rows_text("#bg-list .info-row")
    assert "bg-7777" in bg_str and "terminal" in bg_str, bg_str
    assert "pip install textual" not in bg_str, "折叠态不应展示参数列表"
    assert any(g in bg_str for g in _SPINNER_FRAMES), f"running 任务应有轮播字形: {bg_str}"
    await pilot.pause(0.5)  # 覆盖 ≥2 次 0.1s 动画 tick
    bg_animated = _rows_text("#bg-list .info-row")
    assert bg_animated != bg_str, "running 后台任务应呈动态加载效果（帧未推进）"
    sub_str = _rows_text("#subagents-list .info-row")
    assert "sa-0001" in sub_str and "[thinking]" in sub_str, sub_str
    assert "summarize the asyncio doc" not in sub_str, "折叠态不应展示 description"
    assert any(g in sub_str for g in _SPINNER_FRAMES), f"运行中子代理应有轮播字形: {sub_str}"
    # description 默认不展示，点行展开后以灰色补上全文，再点收起
    sub_row = next(r for r in app.query("#subagents-list .info-row.-expandable")
                   if "sa-0001" in str(r.render()))
    sub_row.scroll_visible(animate=False, top=True, immediate=True)
    await pilot.pause(0.1)
    await pilot.click(sub_row, offset=(2, 0))
    await pilot.pause(0.1)
    assert "summarize the asyncio doc" in str(sub_row.render()), "展开后应展示 description"
    await pilot.click(sub_row, offset=(2, 0))
    await pilot.pause(0.1)
    assert "summarize the asyncio doc" not in str(sub_row.render()), "再点应收起 description"
    RUNTIME_STATE.update_subagent("sa-0001", "tool", "terminal")
    await pilot.pause(1.3)
    sub_str = _rows_text("#subagents-list .info-row")
    assert "[tool: terminal]" in sub_str, f"子代理阶段切换未上屏: {sub_str}"
    RUNTIME_STATE.remove_subagent("sa-0001")
    await pilot.pause(1.3)
    assert _rows_text("#subagents-list .info-row") == "(Empty)", "子代理结束后应显示 (Empty)"
    RUNTIME_STATE.complete_background("bg-7777", ToolResult(content="(smoke finished)"))
    await pilot.pause(1.3)
    bg_str = _rows_text("#bg-list .info-row")
    assert "● bg-7777" in bg_str, f"bg 完成态未上屏: {bg_str}"
    assert not any(g in bg_str for g in _SPINNER_FRAMES), f"completed 后轮播应停: {bg_str}"
    # 参数默认不展示，点行展开后以灰色补上全文（列表里可能还有演示回合遗留的其它任务，
    # 目标行先滚入视口再点；窄面板下行会高于视口，故钉到顶部后点首行，避免点行尾触到屏幕外）
    bg_row = next(r for r in app.query("#bg-list .info-row.-expandable")
                  if "bg-7777" in str(r.render()))
    assert "very/long/remote/path" not in str(bg_row.render()) and "…" not in str(bg_row.render()), \
        "折叠态不应展示参数列表"
    bg_row.scroll_visible(animate=False, top=True, immediate=True)
    await pilot.pause(0.1)
    await pilot.click(bg_row, offset=(2, 0))
    await pilot.pause(0.1)
    expanded_bg = str(bg_row.render())
    assert expanded_bg.startswith("▾ ● bg-7777") and "very/long/remote/path" in expanded_bg, \
        f"展开应显示未截断调用: {expanded_bg}"
    bg_row.scroll_visible(animate=False, top=True, immediate=True)
    await pilot.pause(0.1)
    await pilot.click(bg_row, offset=(2, 0))
    await pilot.pause(0.1)
    assert "very/long/remote/path" not in str(bg_row.render()), "再点应收起参数列表"
    await pilot.click(bg_section, offset=(2, 0))  # 点边框标题折叠 bg 分区（卡片收成只剩上下边框）
    assert bg_section.has_class("-collapsed")
    assert str(bg_section.border_title).startswith("▶ Background Tasks")
    assert not app.query_one("#bg-list", VerticalScroll).display, "折叠后列表应隐藏"
    await pilot.click(bg_section, offset=(2, 0))  # 再点展开
    assert not bg_section.has_class("-collapsed")
    assert str(bg_section.border_title).startswith("▼ Background Tasks")
    assert app.query_one("#bg-list", VerticalScroll).display, "展开后列表应恢复"
    await pilot.click(sub_section, offset=(2, 0))  # 点边框标题折叠 Subagents 分区
    assert sub_section.has_class("-collapsed")
    assert str(sub_section.border_title).startswith("▶ Subagents")
    assert not app.query_one("#subagents-list", VerticalScroll).display, "折叠后列表应隐藏"
    await pilot.click(sub_section, offset=(2, 0))  # 再点展开
    assert not sub_section.has_class("-collapsed")
    assert app.query_one("#subagents-list", VerticalScroll).display, "展开后列表应恢复"
    # 右栏左缘 »/« 标签常驻：点击折叠/展开整个右栏（Information 标题已去除，标签为唯一开关）
    tab = app.query_one("#info-tab-glyph", Static)
    assert str(tab.render()) == "»" and "Collapse" in str(tab.tooltip), (tab.render(), tab.tooltip)
    await pilot.click(tab)
    await pilot.pause(0.1)
    assert app.query_one("#right", Vertical).has_class("-collapsed")
    assert str(tab.render()) == "«" and "Expand" in str(tab.tooltip), (tab.render(), tab.tooltip)
    await pilot.click(tab)
    await pilot.pause(0.1)
    assert not app.query_one("#right", Vertical).has_class("-collapsed")
    assert str(tab.render()) == "»", tab.render()
    # 标签固定在右栏左缘：展开时在分栏线旁（x 小于面板卡片左缘，而非屏幕最右）
    assert tab.region.x < app.size.width - 1, f"标签未在右栏左缘: x={tab.region.x}, 宽={app.size.width}"

    # Ctrl+←/→ 调宽右栏（绑定在 _CommandInput，焦点须在主输入条）：默认 20%、每档 10%、
    # 夹在 20%–40%；左键加宽、右键收窄；折叠态忽略；折叠/展开保留调整值（内联宽度优先于 CSS 折叠规则）
    right = app.query_one("#right", Vertical)
    app.query_one("#prompt").focus()
    await pilot.pause(0.1)
    assert app._panel_pct is None, "初始应为未调整（默认 1fr）"
    await pilot.press("ctrl+left")
    await pilot.pause(0.1)
    assert app._panel_pct == 30 and right.size.width == int(app.size.width * 0.3), \
        (app._panel_pct, right.size.width, app.size.width)
    await pilot.press("ctrl+right")
    await pilot.pause(0.1)
    assert app._panel_pct == 20 and right.size.width == int(app.size.width * 0.2), \
        (app._panel_pct, right.size.width, app.size.width)
    for _ in range(10):  # 连按收窄：夹在下界 20%
        await pilot.press("ctrl+right")
    await pilot.pause(0.1)
    assert app._panel_pct == 20, app._panel_pct
    for _ in range(10):  # 连按加宽：夹在上界 40%
        await pilot.press("ctrl+left")
    await pilot.pause(0.1)
    assert app._panel_pct == 40, app._panel_pct
    await pilot.click(tab)  # 折叠：收 1 列，组合键忽略
    await pilot.pause(0.1)
    await pilot.press("ctrl+right")
    await pilot.pause(0.1)
    assert right.has_class("-collapsed") and app._panel_pct == 40, "折叠态调宽应被忽略"
    await pilot.click(tab)  # 展开：还原调整值 40%
    await pilot.pause(0.1)
    assert right.size.width == int(app.size.width * 0.4), (right.size.width, app.size.width)
    for _ in range(2):  # 复位为默认档，后续分区不受宽度影响
        await pilot.press("ctrl+right")
    await pilot.pause(0.1)
    assert app._panel_pct == 20, app._panel_pct
    # 底部组合键提示：一行暗灰，随右栏折叠一起隐藏（在 #info-panel 内）
    hint = app.query_one("#info-hint", Static)
    assert "resize panel" in str(hint.render()), hint.render()
    assert hint.region.y + 1 == app.query_one("#right", Vertical).region.bottom, "提示应钉在右栏底部"
    await pilot.click(tab)
    await pilot.pause(0.1)
    assert not app.query_one("#info-panel").display, "折叠后提示应随面板隐藏"
    await pilot.click(tab)
    await pilot.pause(0.1)
    # 空态：三区数据清空后各显示 (Empty)（bg 列表还留有演示回合的任务，临时清空再还原）
    saved = RUNTIME_STATE.snapshot()
    RUNTIME_STATE.clear()  # 空态检查（仅冒烟进程内生效）
    await pilot.pause(1.3)  # 覆盖 ≥1 次 1s 轮询
    for _key in ("todos", "bg", "subagents"):
        _rows = _rows_text(f"#{_key}-list .info-row")
        assert _rows == "(Empty)", f"空分区应显示 (Empty): #{_key}-list = {_rows!r}"
    # 还原，避免影响后续分区
    RUNTIME_STATE.set_todos(saved.todos)
    for _bg in saved.background:
        RUNTIME_STATE.register_background(_bg.id, _bg.tool_call)
        if _bg.status == "completed":
            RUNTIME_STATE.complete_background(_bg.id, ToolResult(content=""))
    for _sa in saved.subagents:
        RUNTIME_STATE.register_subagent(_sa.id, _sa.description)
        RUNTIME_STATE.update_subagent(_sa.id, _sa.phase, _sa.detail)
    print("[smoke] right info sections OK: todos/bg/subagents lists live-sync + independent collapse")
