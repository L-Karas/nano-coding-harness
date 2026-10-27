"""冒烟自检 · /extensions：来源 tag / 可选描述 / hook 与未启用原因 / 搜索 / Enter 不关窗 /
Esc 关闭 / 空 registry toast 提示。"""
from __future__ import annotations

import core.extension.registry as registry
from textual.widgets import Input, OptionList, Static

from core.extension import list_extensions
from core.tui.screens import ExtensionsScreen
from smoke._util import prompt_of, screen_option_texts, settle, type_query


async def run(app, pilot) -> None:
    prompt = prompt_of(app)
    original = list_extensions()
    registry.reset()
    registry.record("smoke-builtin", "builtin", enabled=True, events=["before_tool"])
    registry.record("smoke-missing", "user", enabled=False, detail="missing extension.json")
    registry.record("smoke-off", "user", enabled=False, detail="disabled",
                    description="Smoke disabled extension")
    registry.record("smoke-on", "user", enabled=True, events=["before_llm", "after_tool"],
                    description="Smoke enabled extension")
    try:
        # /ext 补全到 /extensions，Enter 打开只读弹窗；行 = 状态字形 名称 [来源] + 可选描述 +
        # hook / 未启用原因（未启用以同 /mcp failed server 的红色 ○ 标示）
        prompt.focus()
        await type_query(prompt, pilot, "/ext")
        assert prompt._candidates == ["/extensions"], prompt._candidates
        await pilot.press("enter")
        await settle(pilot, 10)
        assert not app._busy, "/extensions 不应触发 agent 回合"
        assert app._exception is None, f"渲染异常: {app._exception}"
        screen = app.screen_stack[-1]
        assert isinstance(screen, ExtensionsScreen), "/extensions 未打开扩展弹窗"
        # 列表背景透明（同 /skills），且 height:1fr 撑满主体、提示行钉在窗口最下方
        lst = screen.query_one("#extensions-list", OptionList)
        hint = screen.query_one(".picker-hint", Static)
        picker = screen.query_one(".picker")
        assert lst.styles.background.is_transparent, lst.styles.background
        assert hint.region.bottom == picker.content_region.bottom, \
            f"提示行应钉在窗口最下方: {hint.region} / {picker.content_region}"
        assert screen_option_texts(app, "#extensions-list") == [
            "● smoke-builtin [builtin]\nhooks: before_tool",
            "○ smoke-missing [user]\nmissing extension.json",
            "○ smoke-off [user]\nSmoke disabled extension\ndisabled",
            "● smoke-on [user]\nSmoke enabled extension\nhooks: before_llm, after_tool",
        ], screen_option_texts(app, "#extensions-list")
        # 搜索栏过滤名称（忽略大小写）；清空恢复
        search = screen.query_one("#search-input", Input)
        assert search.placeholder == "Search extensions…" and search.has_focus
        search.value = "SMOKE-ON"
        await settle(pilot)
        assert screen_option_texts(app, "#extensions-list") == [
            "● smoke-on [user]\nSmoke enabled extension\nhooks: before_llm, after_tool"
        ], screen_option_texts(app, "#extensions-list")
        search.value = ""
        await settle(pilot)
        assert len(screen_option_texts(app, "#extensions-list")) == 4, "清空搜索应恢复全部扩展"
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
        # 空 registry：toast 提示而非空弹窗
        registry.reset()
        prompt.focus()
        prompt.text = "/extensions"
        await pilot.press("enter")
        await settle(pilot, 10)
        assert not isinstance(app.screen_stack[-1], ExtensionsScreen), "空 registry 不应开空弹窗"
        toasts = [n.message for n in app._notifications]
        assert any("No extensions found" in message for message in toasts), toasts
        print("[smoke] /extensions OK: descriptions, source tags, disabled reasons, search, "
              "read-only, empty notice")
    finally:
        registry.reset()
        for entry in original:
            registry.record(entry.name, entry.origin, enabled=entry.enabled,
                            detail=entry.detail, events=entry.events,
                            description=entry.description)
