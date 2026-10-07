"""冒烟自检 · /provider：配置状态行 / 掩码 API Key 录入 / 原地确认删除。"""
from __future__ import annotations

from textual.widgets import Input, OptionList, Static

from core.tui.screens import ApiKeyScreen, ProviderScreen
from smoke._util import prompt_of, settle


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # /provider：ProviderScreen（OptionList 弹窗，同会话弹窗组件）行 = 后端 provider
    # 原名 + 配置状态，未配置灰 / 已配置暗绿；遮罩半透明 → 窗外透出主界面（不整屏盖死）；
    # Esc 关闭；实际取数失败（演示环境无 openai）时提示卡而非崩溃
    app.push_screen(ProviderScreen([("deepseek", False), ("qwen", True)]))
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], ProviderScreen)
    # 遮罩半透明：弹窗打开时主界面（screen 栈底层）仍参与合成，窗外可见主界面内容
    assert app.screen.styles.background.a < 1, "弹窗遮罩应半透明（窗外透出主界面）"
    pv = app.screen_stack[-1].query_one("#provider-list", OptionList)
    pv_rows = [pv.get_option_at_index(i).prompt.plain for i in range(pv.option_count)]
    assert pv_rows == ["deepseek [○ unconfigured]", "qwen [● configured]"], pv_rows
    # 行末 span = 配置状态段（Option prompt 直接持原始 hex 色：灰 #94a3b8 / 暗绿 #16a34a）
    for i, marker in ((0, "#94a3b8"), (1, "#16a34a")):
        spans = pv.get_option_at_index(i).prompt.spans
        assert marker in str(spans[-1].style), f"状态色错误: {spans[-1].style}"
    # ↑/↓ 首尾循环：首行 ↑ 跳末行、末行 ↓ 回首行（OptionList 原生循环，同会话弹窗）
    assert pv.highlighted == 0
    await pilot.press("up")
    assert pv.highlighted == pv.option_count - 1, f"首行按 ↑ 应跳末行: {pv.highlighted}"
    await pilot.press("down")
    assert pv.highlighted == 0, f"末行按 ↓ 应回首行: {pv.highlighted}"
    # 顶部搜索栏：按 provider 名子串过滤（忽略大小写）；清空恢复；仅下边框 + 默认聚焦
    p_search = app.screen_stack[-1].query_one("#search-input", Input)
    assert p_search.placeholder == "Search providers…" and p_search.has_focus, p_search.placeholder
    p_row = app.screen_stack[-1].query_one("#search-row")
    assert not p_row.styles.border_top[0] and p_row.styles.border_bottom[0] == "solid"
    p_search.value = "QWE"
    await settle(pilot)
    assert [pv.get_option_at_index(i).prompt.plain for i in range(pv.option_count)] == \
        ["qwen [● configured]"], "搜索应忽略大小写并只留匹配行"
    p_search.value = ""
    await settle(pilot)
    assert pv.option_count == 2, "清空搜索应恢复全部 provider"
    # Enter 配置 API Key：选中行 Enter → ApiKeyScreen（掩码输入），
    # 再次 Enter 经 core.model.configure_provider 落盘并重取列表刷新行状态
    # （演示环境可无 openai：懒导入失败则跳过深流程断言，只验证 Esc 关闭）
    try:
        from core.client import configure_provider  # noqa: F401
    except Exception:
        pass
    else:
        import core.client as _cm
        _calls, _dels = [], []
        # 拦写盘 + 翻转内存态：get_provider_list 返回翻转后的状态，
        # 验证 UI 配置变更后重取数据源刷新行（而非本地镜像）
        _state = [("deepseek", False), ("qwen", True)]
        _cm.get_provider_list = lambda: list(_state)

        def _flip(p: str, configured: bool) -> None:
            _state[:] = [(n, configured if n == p else c) for n, c in _state]

        _cm.configure_provider = lambda p, k: (_calls.append((p, k)), _flip(p, True))
        _cm.unconfigure_provider = lambda p: (_dels.append(p), _flip(p, False))
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen_stack[-1], ApiKeyScreen), \
            "选中行 Enter 未弹出 API Key 录入窗"
        api_in = app.screen_stack[-1].query_one("#api-key-input", Input)
        assert api_in.has_focus, "API Key 输入框应自动聚焦"
        # 搜索栏式字段行：❯ 标记 + 仅下边框（边框画在行容器上）
        api_row = api_in.parent
        assert not api_row.styles.border_top[0] and api_row.styles.border_bottom[0] == "solid", \
            f"API Key 字段行应为搜索栏式下边框: {(api_row.styles.border_top, api_row.styles.border_bottom)}"
        assert str(app.screen_stack[-1].query_one(".field-mark", Static).render()) == "❯"
        api_in.value = "sk-test-12345"
        await settle(pilot)
        shown = api_in._value.plain  # 直接进入渲染 Strip 的掩码文本
        assert shown == "•" * len("sk-test-12345"), f"输入应以掩码显示: {shown!r}"
        assert "sk-test" not in shown, "API Key 明文泄露"
        await pilot.press("enter")  # 提交
        await settle(pilot)
        assert _calls == [("deepseek", "sk-test-12345")], _calls
        assert not isinstance(app.screen_stack[-1], ApiKeyScreen), "提交后录入窗应关闭"
        pv = app.screen_stack[-1].query_one("#provider-list", OptionList)
        spans = pv.get_option_at_index(0).prompt.spans
        assert "#16a34a" in str(spans[-1].style), f"行状态未就地刷新为已配置: {spans[-1].style}"
        print("[smoke] /provider Enter OK: masked API Key input configures provider")
        # 过滤后 Delete：目标仍按行 id 回查（列表位置已不对应 _rows，旧实现会删错 provider）
        p_search = app.screen_stack[-1].query_one("#search-input", Input)
        p_search.value = "QWE"
        await settle(pilot)
        await pilot.press("delete")
        await settle(pilot)
        filter_hint = str(app.screen_stack[-1].query_one("#confirm-hint", Static).render())
        assert "Remove key for qwen?" in filter_hint, f"过滤后 Delete 目标错误: {filter_hint!r}"
        await pilot.press("escape")
        await settle(pilot)
        p_search.value = ""
        await settle(pilot)
        print("[smoke] /provider filtered Delete OK: targets the row behind the option id")
        # Delete 删除选中行配置：窗内底部红字原地确认（无确认弹窗；Esc 撤销不删），
        # 确认后 unconfigure_provider 落盘 + 行状态回未配置（灰）；
        # 已未配置行再按 Delete 为空操作（幂等，不落盘不报错）
        await pilot.press("delete")
        await settle(pilot)
        assert isinstance(app.screen_stack[-1], ProviderScreen), "Delete 不应再弹确认窗"
        phint = app.screen_stack[-1].query_one("#confirm-hint", Static)
        pmsg = phint.render()
        assert "Remove key for deepseek?" in str(pmsg) \
            and "rgb(248,113,113)" in str(pmsg.spans[0].style), \
            f"Delete 未就地显示红色确认: {pmsg!r}"
        await pilot.press("escape")  # 撤销确认：提示复原、不落盘
        await settle(pilot)
        assert _dels == [] and app._exception is None, f"取消确认不应删除: {_dels}"
        assert str(phint.render()) == \
            "  ↑/↓ browse    Enter set API Key    Delete remove config    Esc close", \
            "撤销确认后应恢复原提示行"
        await pilot.press("delete")
        await settle(pilot)
        await pilot.press("enter")  # 确认删除
        await settle(pilot)
        assert _dels == ["deepseek"], f"Delete 未删除选中 provider: {_dels}"
        pv = app.screen_stack[-1].query_one("#provider-list", OptionList)
        spans = pv.get_option_at_index(0).prompt.spans
        assert "#94a3b8" in str(spans[-1].style), f"Delete 后行状态未刷新为未配置: {spans[-1].style}"
        await pilot.press("delete")  # 行已未配置：再按 Delete 应空操作
        await settle(pilot)
        assert _dels == ["deepseek"] and app._exception is None, f"重复 Delete 不应落盘: {_dels}"
        print("[smoke] /provider Delete OK: unconfigures selected provider in place")
        await pilot.press("enter")  # 再开一行：Esc 应取消且不落盘
        await settle(pilot)
        assert isinstance(app.screen_stack[-1], ApiKeyScreen)
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen_stack[-1], ApiKeyScreen) and _calls == [
            ("deepseek", "sk-test-12345")], f"Esc 取消不应触发配置: {_calls}"
        print("[smoke] /provider Esc cancels API Key entry without persisting")
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], ProviderScreen), "Esc 未关闭 ProviderScreen"
    prompt.text = "/provider"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/provider 不应触发 agent 回合"
    assert app._exception is None, f"渲染异常: {app._exception}"
    if isinstance(app.screen_stack[-1], ProviderScreen):  # 真实数据源可用：能打开弹窗
        await pilot.press("escape")
        await settle(pilot)
    else:  # 数据源不可用（演示环境）：应给出 Provider List 提示卡
        bodies = [str(w.render()) for w in app.query(".card-body")]
        assert any("Provider List" in b for b in bodies), bodies
    print("[smoke] /provider OK: backend provider name + colored config status in OptionList")
