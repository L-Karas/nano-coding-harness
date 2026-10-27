"""冒烟自检 · /skills：技能列表行格式 / 首尾循环 / Enter 发送 [Invoke skill] /
Tab 填入调用信息并附带补充消息 / 空注册表提示。"""
from __future__ import annotations

from textual.widgets import Input, OptionList

from core.skill.skills import Skill
from core.tui.screens import SkillsScreen
from smoke._util import prompt_of, settle


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # 注入两个模拟技能（真实磁盘技能同时在场）；段末原样放回，不影响进程内其它逻辑
    import core.skill.skills as _skills_mod
    import core.tui.screens.skills as _skills_screen

    _skills_orig = dict(_skills_mod.SKILL_REGISTRY)
    # 冒烟不读写真实 .skills.json：把启用状态替换为内存集合，段末恢复
    _skills_is_enabled_orig = _skills_screen.is_skill_enabled
    _skills_set_enabled_orig = _skills_screen.set_skill_enabled
    _skills_state_error_orig = _skills_screen.skill_state_error
    _smoke_disabled: set[str] = set()

    def _smoke_is_enabled(name: str) -> bool:
        return name not in _smoke_disabled

    def _smoke_state_error():
        return None

    def _smoke_set_enabled(name: str, enabled: bool):
        if enabled:
            _smoke_disabled.discard(name)
        else:
            _smoke_disabled.add(name)
        action = "Enabled" if enabled else "Disabled"
        return True, f"{action} skill '{name}'"

    _skills_screen.is_skill_enabled = _smoke_is_enabled
    _skills_screen.set_skill_enabled = _smoke_set_enabled
    _skills_screen.skill_state_error = _smoke_state_error
    _skills_mod.SKILL_REGISTRY.update({
        "smoke-skill-a": Skill(
            name="smoke-skill-a",
            description="writes smoke checks",
            content="",
            location="/smoke/skills/smoke-skill-a",
            skill_type="user",
        ),
        "smoke-skill-b": Skill(
            name="smoke-skill-b",
            description="example skill description",
            content="",
            location="/smoke/skills/smoke-skill-b",
            skill_type="user",
        ),
    })

    # /skills：SkillsScreen（OptionList 弹窗，同会话 /provider /model 弹窗组件）
    # 列出全部已扫描技能，行 = ● 技能名（绿色加粗）+ 描述（暗灰完整多行）；Esc 关闭；
    # 无技能时给出 toast 提示而非空弹窗（点击右栏条目后焦点可能已不在输入框：先显式聚焦）
    prompt.focus()
    prompt.text = "/skills"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/skills 不应触发 agent 回合"
    assert app._exception is None, f"渲染异常: {app._exception}"
    assert isinstance(app.screen_stack[-1], SkillsScreen), "/skills 未打开技能弹窗"
    sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
    rows = [sk_lst.get_option_at_index(i).prompt.plain for i in range(sk_lst.option_count)]
    assert any(r.startswith("● smoke-skill-a") and "writes smoke checks" in r for r in rows), rows
    assert any(r.startswith("● smoke-skill-b") and "example skill description" in r for r in rows), rows
    assert any("find-skills" in r for r in rows), "真实磁盘扫描的技能也应列出"
    # 行布局：首行为「● 技能名」，描述自第二行起（strip 后整段插入）
    a_row = next(r for r in rows if r.startswith("● smoke-skill-a"))
    assert a_row.splitlines() == ["● smoke-skill-a [user]", "writes smoke checks"], a_row
    # 行内两段 span：技能名绿色加粗（#4ade80，同 /mcp 弹窗 server 名）、描述次级灰（#94a3b8）
    sk_opt_a = next(sk_lst.get_option_at_index(i) for i in range(sk_lst.option_count)
                    if sk_lst.get_option_at_index(i).prompt.plain.startswith("● smoke-skill-a"))
    spans = sk_opt_a.prompt.spans
    assert "#4ade80" in str(spans[0].style), f"技能名色错误: {spans[0].style}"
    assert "#94a3b8" in str(spans[-1].style), f"描述色错误: {spans[-1].style}"
    # ↑/↓ 首尾循环：首行 ↑ 跳末行、末行 ↓ 回首行（OptionList 原生循环，同其它弹窗）
    assert sk_lst.highlighted == 0
    await pilot.press("up")
    assert sk_lst.highlighted == sk_lst.option_count - 1, f"首行按 ↑ 应跳末行: {sk_lst.highlighted}"
    await pilot.press("down")
    assert sk_lst.highlighted == 0, f"末行按 ↓ 应回首行: {sk_lst.highlighted}"
    # 顶部搜索栏：占位提示 + 默认聚焦；仅下边框（上边框无）；名称子串过滤忽略大小写，清空恢复
    sk_search = app.screen_stack[-1].query_one("#search-input", Input)
    assert sk_search.placeholder == "Search skills…", sk_search.placeholder
    assert sk_search.has_focus, "搜索栏应默认聚焦"
    sk_row = app.screen_stack[-1].query_one("#search-row")
    assert not sk_row.styles.border_top[0] and sk_row.styles.border_bottom[0] == "solid", \
        f"搜索栏应仅显示下边框: {(sk_row.styles.border_top, sk_row.styles.border_bottom)}"
    assert str(app.screen_stack[-1].query_one("#search-prompt").render()) == "❯", \
        "搜索栏缺 ❯ 标记"
    sk_search.value = "SMOKE-SKILL-A"  # 大写：验证忽略大小写
    await settle(pilot)
    rows = [sk_lst.get_option_at_index(i).prompt.plain for i in range(sk_lst.option_count)]
    assert rows == ["● smoke-skill-a [user]\nwrites smoke checks"], rows
    sk_search.value = ""
    await settle(pilot)
    assert sk_lst.option_count >= 2, "清空搜索应恢复全部技能"
    print("[smoke] /skills search OK: case-insensitive name filter, bottom-border-only bar")
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], SkillsScreen), "Esc 未关闭 SkillsScreen"
    # Enter 选中技能：填入 "[Invoke skill '<name>']" 并以用户消息发出（弹窗关闭、
    # 进入回合）；Esc（dismiss None）不发送
    prompt.focus()
    prompt.text = "/skills"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], SkillsScreen)
    sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
    sk_idx = next(i for i in range(sk_lst.option_count)
                  if sk_lst.get_option_at_index(i).prompt.plain.startswith("● smoke-skill-a"))
    sk_lst.highlighted = sk_idx
    await pilot.press("enter")
    await settle(pilot, 5)
    assert not isinstance(app.screen_stack[-1], SkillsScreen), "Enter 选中技能后弹窗应关闭"
    assert app._busy, "技能调用应以用户回合发出"
    bodies = [str(w.render()) for w in app.query(".card-body")]
    assert any("[Invoke skill 'smoke-skill-a']" in b for b in bodies), bodies
    for _ in range(200):  # 等回合结束（demo agent 流式回复）
        if not app._busy:
            break
        await pilot.pause(0.05)
    assert not app._busy, "技能回合未结束"
    print("[smoke] /skills Enter OK: fills & sends [Invoke skill '<name>']")
    # Tab：不发送；把 "[Invoke skill '<name>'] " 填入输入栏并聚焦、光标置尾，
    # 用户可继续写补充消息后一起发送（提示行文案为 invoke with message）
    prompt.focus()
    prompt.text = "/skills"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], SkillsScreen)
    assert app.screen_stack[-1].HINT == \
        "  ↑/↓ browse    Enter/click invoke    Tab invoke with message" \
        "    Ctrl+E enable/disable    Esc close", \
        app.screen_stack[-1].HINT
    sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
    sk_idx = next(i for i in range(sk_lst.option_count)
                  if sk_lst.get_option_at_index(i).prompt.plain.startswith("● smoke-skill-a"))
    sk_lst.highlighted = sk_idx
    await pilot.press("tab")
    await settle(pilot, 5)
    assert not isinstance(app.screen_stack[-1], SkillsScreen), "Tab 选中技能后弹窗应关闭"
    assert not app._busy, "Tab 不应触发回合"
    assert prompt.has_focus, "Tab 后应聚焦输入栏"
    assert prompt.text == "[Invoke skill 'smoke-skill-a'] ", repr(prompt.text)
    assert prompt.cursor_location == prompt.document.end, \
        f"光标应在填入文本末尾: {prompt.cursor_location} / {prompt.document.end}"
    await pilot.press("e", "x", "t", "r", "a")  # 继续输入补充消息
    await settle(pilot, 3)
    assert prompt.text == "[Invoke skill 'smoke-skill-a'] extra", repr(prompt.text)
    await pilot.press("enter")
    await settle(pilot, 5)
    assert app._busy, "补充消息应随技能调用一起发出"
    bodies = [str(w.render()) for w in app.query(".card-body")]
    assert any("[Invoke skill 'smoke-skill-a'] extra" in b for b in bodies), bodies
    for _ in range(200):  # 等回合结束（demo agent 流式回复）
        if not app._busy:
            break
        await pilot.pause(0.05)
    assert not app._busy, "技能补充消息回合未结束"
    print("[smoke] /skills Tab OK: fills invoke message into prompt, extra message sent together")
    # Ctrl+E：禁用当前技能（行变红 ○）→ Enter 只提示不关窗 → 再次 Ctrl+E 恢复
    prompt.focus()
    prompt.text = "/skills"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], SkillsScreen)
    sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
    sk_idx = next(i for i in range(sk_lst.option_count)
                  if sk_lst.get_option_at_index(i).prompt.plain.startswith("● smoke-skill-a"))
    sk_lst.highlighted = sk_idx
    await pilot.press("ctrl+e")
    await settle(pilot)
    rows = [sk_lst.get_option_at_index(i).prompt.plain for i in range(sk_lst.option_count)]
    assert any(r.startswith("○ smoke-skill-a") for r in rows), rows
    assert "smoke-skill-a" in _smoke_disabled
    await pilot.press("enter")
    await settle(pilot, 5)
    assert isinstance(app.screen_stack[-1], SkillsScreen), "禁用技能 Enter 不应关窗"
    assert not app._busy, "禁用技能 Enter 不应触发回合"
    await pilot.press("ctrl+e")
    await settle(pilot)
    rows = [sk_lst.get_option_at_index(i).prompt.plain for i in range(sk_lst.option_count)]
    assert any(r.startswith("● smoke-skill-a") for r in rows), rows
    assert "smoke-skill-a" not in _smoke_disabled
    await pilot.press("escape")
    await settle(pilot)
    print("[smoke] /skills Ctrl+E OK: toggles enabled/disabled, disabled Enter is rejected")
    _skills_mod.SKILL_REGISTRY.clear()  # 无技能场景：应给 toast 提示而非空弹窗
    prompt.focus()
    prompt.text = "/skills"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy and not isinstance(app.screen_stack[-1], SkillsScreen), \
        "无技能时不应打开空弹窗"
    toasts = [n.message for n in app._notifications]
    assert any("No skills found" in message for message in toasts), toasts
    _skills_mod.SKILL_REGISTRY.update(_skills_orig)  # 复原（原样放回，含磁盘扫描项）
    _skills_screen.is_skill_enabled = _skills_is_enabled_orig
    _skills_screen.set_skill_enabled = _skills_set_enabled_orig
    _skills_screen.skill_state_error = _skills_state_error_orig
    print("[smoke] /skills OK: picker-style popup lists all skills, empty registry notifies")
