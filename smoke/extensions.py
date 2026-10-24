"""冒烟自检 · /extensions：来源 tag / hook 与未启用原因 / 搜索 / Enter 不关窗 / Esc 关闭 /
空 registry 提示卡。"""
from __future__ import annotations

import core.extension.registry as registry
from textual.widgets import Input, OptionList

from core.extension import list_extensions
from core.tui.screens import ExtensionsScreen
from smoke._util import prompt_of, settle, type_query


def _rows(app) -> list[str]:
    lst = app.screen_stack[-1].query_one("#extensions-list", OptionList)
    return [lst.get_option_at_index(i).prompt.plain for i in range(lst.option_count)]


async def run(app, pilot) -> None:
    prompt = prompt_of(app)
    original = list_extensions()
    registry.reset()
    registry.record("smoke-builtin", "builtin", enabled=True, events=["before_tool"])
    registry.record("smoke-off", "user", enabled=False, detail="missing extension.json")
    registry.record("smoke-on", "user", enabled=True, events=["before_llm", "after_tool"])
    try:
        # /ext 补全到 /extensions，Enter 打开只读弹窗；行 = ● 名称 [来源] + hook / 未启用原因
        prompt.focus()
        await type_query(prompt, pilot, "/ext")
        assert prompt._candidates == ["/extensions"], prompt._candidates
        await pilot.press("enter")
        await settle(pilot, 10)
        assert not app._busy, "/extensions 不应触发 agent 回合"
        assert app._exception is None, f"渲染异常: {app._exception}"
        screen = app.screen_stack[-1]
        assert isinstance(screen, ExtensionsScreen), "/extensions 未打开扩展弹窗"
        assert _rows(app) == [
            "● smoke-builtin [builtin]\nhooks: before_tool",
            "● smoke-off [user]\nmissing extension.json",
            "● smoke-on [user]\nhooks: before_llm, after_tool",
        ], _rows(app)
        # 搜索栏过滤名称（忽略大小写）；清空恢复
        search = screen.query_one("#search-input", Input)
        assert search.placeholder == "Search extensions…" and search.has_focus
        search.value = "SMOKE-ON"
        await settle(pilot)
        assert _rows(app) == ["● smoke-on [user]\nhooks: before_llm, after_tool"], _rows(app)
        search.value = ""
        await settle(pilot)
        assert len(_rows(app)) == 3, "清空搜索应恢复全部扩展"
        # 只读：单击 / Enter 都不关窗，Esc 关闭
        await pilot.click("#extensions-list", offset=(2, 0))
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], ExtensionsScreen), "单击行不应关窗"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], ExtensionsScreen), "Enter 不应关窗"
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen_stack[-1], ExtensionsScreen), "Esc 未关闭扩展弹窗"
        # 空 registry：提示卡而非空弹窗
        registry.reset()
        prompt.focus()
        prompt.text = "/extensions"
        await pilot.press("enter")
        await settle(pilot, 10)
        assert not isinstance(app.screen_stack[-1], ExtensionsScreen), "空 registry 不应开空弹窗"
        bodies = [str(w.render()) for w in app.query(".card-body")]
        assert any("No extensions found" in body for body in bodies), bodies
        print("[smoke] /extensions OK: source tags, disabled reason, search, read-only, empty notice")
    finally:
        registry.reset()
        for entry in original:
            registry.record(entry.name, entry.origin, enabled=entry.enabled,
                            detail=entry.detail, events=entry.events)
