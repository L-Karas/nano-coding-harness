"""冒烟自检 · /login（别名 /logout）：四项菜单 / 注册表单 / 注销列表与原地确认。"""
from __future__ import annotations

from textual.widgets import Input, OptionList, Select, Static, Switch

from core.tui.screens import (
    LoginScreen,
    RegisterModelScreen,
    RegisterProviderScreen,
    UnregisterModelScreen,
    UnregisterProviderScreen,
)
from smoke._util import prompt_of, settle, type_query


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # /login（别名 /logout）：LoginScreen 四项菜单（注册/注销自定义提供方与模型），
    # 行 = 暗灰序号 + 绿（注册）/红（注销）动作文本；选项 1 → RegisterProviderScreen
    # 三输入表单（Tab 切换字段），Enter 经 core.client.login_provider 落盘注册后关窗回菜单；
    # 选项 2 → RegisterModelScreen（下拉/模型名/开关）经 login_model 注册；
    # 选项 3/4 → UnregisterProviderScreen / UnregisterModelScreen 列表，Delete 在窗内
    # 红字原地确认（再 Enter）分别经 logout_provider / logout_model 注销；注销后留在
    # 窗内刷新可连续操作；空字段/空列表留在窗内提示；Esc 逐层关闭
    await type_query(prompt, pilot, "/logo")  # 别名 /logout 前缀同样命中 /login 行
    assert prompt._candidates == ["/login"], prompt._candidates
    await pilot.press("enter")  # Enter 应用候选（/login）并提交 → 打开菜单
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], LoginScreen), "Enter /login 未打开 LoginScreen"
    lg = app.screen_stack[-1].query_one("#login-list", OptionList)
    assert [lg.get_option_at_index(i).prompt.plain for i in range(lg.option_count)] == [
        "1. Register custom provider",
        "2. Register custom model",
        "3. Unregister custom provider",
        "4. Unregister custom model"], "登录菜单四项文案错误"
    # 行内两段 span：序号暗灰 #64748b；注册绿 #4ade80 / 注销红 #f87171
    assert "#64748b" in str(lg.get_option_at_index(0).prompt.spans[0].style)
    assert "#4ade80" in str(lg.get_option_at_index(0).prompt.spans[-1].style)
    assert "#f87171" in str(lg.get_option_at_index(3).prompt.spans[-1].style)
    await pilot.press("enter")  # 高亮首项 → 注册自定义提供方表单
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], RegisterProviderScreen), \
        "选项 1 未弹出提供方注册表单"
    form = app.screen_stack[-1]
    # 字段行统一搜索栏式：❯ 标记 + 仅下边框（下边框画在行容器上）
    rows = list(form.query(".field-row"))
    assert len(rows) == 3, f"提供方表单应有 3 个字段行: {len(rows)}"
    assert all(not r.styles.border_top[0] and r.styles.border_bottom[0] == "solid" for r in rows)
    assert [str(m.render()) for m in form.query(".field-mark")] == ["❯"] * 3
    # ↑/↓ 切字段（此前本表单缺 _ArrowNav，只有 Tab 生效）
    form.query_one("#reg-provider-name", Input).focus()
    await settle(pilot)
    await pilot.press("down")
    await settle(pilot)
    assert form.focused.id == "reg-provider-url", f"↓ 应切到下一字段: {form.focused.id}"
    await pilot.press("up")
    await settle(pilot)
    assert form.focused.id == "reg-provider-name", f"↑ 应切回上一字段: {form.focused.id}"
    await pilot.press("enter")  # 空字段：不注册、窗不关
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], RegisterProviderScreen), "空字段不应提交"
    import core.client as _cm5
    _calls5 = []
    _orig_login = _cm5.login_provider
    _cm5.login_provider = lambda p, u, k="": (_calls5.append((p, u, k)), True)[1]
    form.query_one("#reg-provider-name", Input).value = "smoke-provider"
    form.query_one("#reg-provider-url", Input).value = "https://api.smoke.test/v1"
    form.query_one("#reg-provider-key", Input).value = "sk-smoke"
    await pilot.press("enter")
    await settle(pilot)
    assert _calls5 == [("smoke-provider", "https://api.smoke.test/v1", "sk-smoke")], \
        f"Enter 未按三项输入调用 login_provider: {_calls5}"
    assert not isinstance(app.screen_stack[-1], RegisterProviderScreen), "注册成功后表单应关窗"
    assert isinstance(app.screen_stack[-1], LoginScreen), "表单关窗后应回到登录菜单"
    _cm5.login_provider = _orig_login  # 复原
    # 选项 2 → RegisterModelScreen：提供方下拉取自 get_provider_list(custom_provider=True)
    # （默认选中首项）+ 模型名 + 上下文长度/最大输出 + 思考/视觉开关，Enter 经
    # core.client.login_model 落盘注册
    _providers6 = [("smoke-provider", True)]
    _calls6 = []
    _orig_get_providers6 = _cm5.get_provider_list
    _orig_login_model6 = _cm5.login_model

    def _fake_login_model(p, m, thinking=True, vision=True, context_length=0, max_output=None):
        _calls6.append((p, m, thinking, vision, context_length, max_output))
        return True

    _cm5.get_provider_list = lambda custom_provider=False: list(_providers6)
    _cm5.login_model = _fake_login_model
    await pilot.press("down")  # 高亮移到 2. Register custom model
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], RegisterModelScreen), \
        "选项 2 未弹出模型注册表单"
    mform = app.screen_stack[-1]
    msel = mform.query_one("#reg-model-provider", Select)
    assert [v for _p, v in msel._options if v is not Select.NULL] == ["smoke-provider"], \
        "提供方下拉未来自 get_provider_list(custom_provider=True)"
    assert msel.selection is None, f"提供方下拉应默认未选择: {msel.value!r}"
    assert msel.prompt == "Select a custom provider", f"下拉提示文案应表明是选择: {msel.prompt!r}"
    assert msel.has_focus, "表单应聚焦提供方下拉（先选提供方）"
    # 键盘导航：未展开时 ↑/↓ 切字段；Enter 展开浮层，Esc 只关浮层（选值不变）
    await pilot.press("down")
    await settle(pilot)
    assert not msel.expanded and mform.focused.id == "reg-model-name", \
        f"↓ 应切到下一字段而非展开下拉: {mform.focused.id}"
    await pilot.press("up")
    await settle(pilot)
    assert mform.focused is msel, "↑ 应切回提供方下拉"
    await pilot.press("enter")
    await settle(pilot)
    assert msel.expanded, "Enter 应展开提供方下拉"
    # 浮层滑块同 /sessions 列表（app.css 同一组规则）：1 列宽 + 暗灰滑块 #475569
    ov = msel.query_one("SelectOverlay")
    assert ov.styles.scrollbar_size_vertical == 1 and ov.styles.scrollbar_color.hex.lower() == "#475569", \
        (ov.styles.scrollbar_size_vertical, ov.styles.scrollbar_color)
    await pilot.press("escape")
    await settle(pilot)
    assert not msel.expanded and msel.selection is None and app.screen_stack[-1] is mform, \
        "Esc 应只关下拉浮层（不选值、不关窗）"
    # 未选择时经模型名输入提交：留在窗内提示，不得把空选择传给 login_model
    mform.query_one("#reg-model-name", Input).focus()
    mform.query_one("#reg-model-name", Input).value = "smoke-model"
    await pilot.press("enter")
    await settle(pilot)
    assert app.screen_stack[-1] is mform, "未选 provider 应留在窗内"
    assert _calls6 == [], f"未选 provider 不应调用 login_model: {_calls6}"
    msel.value = "smoke-provider"
    assert mform.query_one("#reg-model-thinking", Switch).value is True
    assert mform.query_one("#reg-model-vision", Switch).value is True
    # 数字字段限制为 integer（非数字按键/粘贴被拒）
    assert mform.query_one("#reg-model-context", Input).type == "integer"
    assert mform.query_one("#reg-model-max-output", Input).type == "integer"
    # 字段行统一搜索栏式：❯ 标记 + 行下边框；行内输入/下拉无自身边框
    mrows = list(mform.query(".field-row"))
    assert len(mrows) == 4, f"模型表单应有 4 个字段行: {len(mrows)}"
    assert all(not r.styles.border_top[0] and r.styles.border_bottom[0] == "solid" for r in mrows)
    assert [str(m.render()) for m in mform.query(".field-mark")] == ["❯"] * 4
    assert not mform.query_one("#reg-model-provider").query_one("SelectCurrent").styles.border_top[0]
    assert not mform.query_one("#reg-model-thinking", Switch).styles.border_top[0]
    assert not mform.query_one("#reg-model-vision", Switch).styles.border_top[0]
    assert mform.query_one("#reg-model-thinking", Switch).styles.background.is_transparent
    assert mform.query_one("#reg-model-vision", Switch).styles.background.is_transparent
    # 开关无自身边框，Tab 聚焦时标签文字变色提示光标（开/关看滑块颜色）
    thinking_sw = mform.query_one("#reg-model-thinking", Switch)
    thinking_label = thinking_sw.parent.query_one(".switch-label", Static)
    thinking_sw.focus()
    await settle(pilot)
    assert thinking_label.styles.color.hex.lower() == "#7287fd", \
        f"聚焦开关时标签应变为聚焦色: {thinking_label.styles.color}"
    mform.query_one("#reg-model-name", Input).focus()  # 交还焦点，后续 Enter 保持提交表单
    mform.query_one("#reg-model-name", Input).value = "smoke-model"
    mform.query_one("#reg-model-context", Input).value = "128000"
    mform.query_one("#reg-model-max-output", Input).value = "32000"
    mform.query_one("#reg-model-thinking", Switch).value = False
    await pilot.press("enter")
    await settle(pilot)
    assert _calls6 == [("smoke-provider", "smoke-model", False, True, 128000, 32000)], \
        f"Enter 未按表单值调用 login_model: {_calls6}"
    assert isinstance(app.screen_stack[-1], LoginScreen), "模型注册关窗后应回到登录菜单"
    # 上下文长度必须为正整数：留空提交留在窗内、不调用 login_model
    await pilot.press("enter")  # 高亮仍在 2 → 再开模型注册表单
    await settle(pilot)
    bad_form = app.screen_stack[-1]
    assert isinstance(bad_form, RegisterModelScreen)
    bad_form.query_one("#reg-model-provider", Select).value = "smoke-provider"
    bad_form.query_one("#reg-model-name", Input).value = "smoke-model"
    bad_form.query_one("#reg-model-name", Input).focus()
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], RegisterModelScreen), "非法上下文长度应留在窗内"
    assert _calls6 == [("smoke-provider", "smoke-model", False, True, 128000, 32000)], \
        f"非法上下文长度不应调用 login_model: {_calls6}"
    await pilot.press("escape")
    await settle(pilot)
    # 无自定义提供方：Select.value 为 Select.NULL（truthy），提交必须被 selection 归一拦下，
    # 不得把 Select.NULL 当 provider 传给 login_model
    _cm5.get_provider_list = lambda custom_provider=False: []
    await pilot.press("enter")  # 高亮仍在 2 → 再开模型注册表单
    await settle(pilot)
    empty_form = app.screen_stack[-1]
    assert isinstance(empty_form, RegisterModelScreen)
    assert empty_form.query_one("#reg-model-provider", Select).selection is None
    empty_form.query_one("#reg-model-name", Input).value = "smoke-model"
    empty_form.query_one("#reg-model-name", Input).focus()
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], RegisterModelScreen), "未选 provider 应留在窗内"
    assert _calls6 == [("smoke-provider", "smoke-model", False, True, 128000, 32000)], \
        f"未选 provider 不应调用 login_model: {_calls6}"
    await pilot.press("escape")
    await settle(pilot)
    _cm5.get_provider_list = _orig_get_providers6
    _cm5.login_model = _orig_login_model6
    # 选项 3 → UnregisterProviderScreen：列表取自 get_provider_list(custom_provider=True)，
    # Enter 选中项经 core.client.logout_provider 注销后关窗回菜单
    _providers7 = [("smoke-a", True), ("smoke-b", False)]
    _calls7 = []
    _orig_get_providers7 = _cm5.get_provider_list
    _orig_logout7 = _cm5.logout_provider
    _cm5.get_provider_list = lambda custom_provider=False: list(_providers7)

    def _fake_logout7(p):
        _calls7.append(p)
        _providers7[:] = [row for row in _providers7 if row[0] != p]
        return True

    _cm5.logout_provider = _fake_logout7
    await pilot.press("down")  # 高亮从 2 移到 3. Unregister custom provider
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], UnregisterProviderScreen), \
        "选项 3 未弹出提供方注销列表"
    ulist = app.screen_stack[-1].query_one("#unreg-provider-list", OptionList)
    assert [ulist.get_option_at_index(i).id for i in range(ulist.option_count)] == \
        ["smoke-a", "smoke-b"], "注销列表未来自 get_provider_list(custom_provider=True)"
    await pilot.press("enter")  # Enter 不再直接注销（统一 Delete 键）
    await settle(pilot)
    assert _calls7 == [] and app._exception is None, f"Enter 不应直接注销: {_calls7}"
    await pilot.press("delete")  # 高亮首项 smoke-a → 窗内红字原地确认
    await settle(pilot)
    uhint = app.screen_stack[-1].query_one("#confirm-hint", Static)
    umsg = uhint.render()
    assert 'Unregister "smoke-a" and its models?' in str(umsg) \
        and "rgb(248,113,113)" in str(umsg.spans[0].style), \
        f"注销提供方未就地显示红色确认: {umsg!r}"
    await pilot.press("escape")  # 撤销确认：不注销
    await settle(pilot)
    assert _calls7 == [] and isinstance(app.screen_stack[-1], UnregisterProviderScreen), \
        f"取消确认不应注销: {_calls7}"
    # 注销窗口本身也需 Esc 退出（回归：曾缺 action_cancel，Esc 无响应、窗口卡死）
    await pilot.press("escape")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], LoginScreen), "Esc 未关闭注销提供方窗口"
    await pilot.press("enter")  # 重新打开注销列表（LoginScreen 高亮仍在 3）
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], UnregisterProviderScreen)
    ulist = app.screen_stack[-1].query_one("#unreg-provider-list", OptionList)  # 重开后是新实例
    await pilot.press("delete")  # 高亮首项 smoke-a → 就地确认
    await settle(pilot)
    await pilot.press("enter")  # 确认注销
    await settle(pilot)
    assert _calls7 == ["smoke-a"], f"Enter 未调用 logout_provider: {_calls7}"
    assert isinstance(app.screen_stack[-1], UnregisterProviderScreen), \
        "注销后不应关闭注销窗口"
    assert [ulist.get_option_at_index(i).id for i in range(ulist.option_count)] == \
        ["smoke-b"], "注销后列表应刷新"
    await pilot.press("escape")  # 手动关窗回菜单
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], LoginScreen), "Esc 未关闭注销提供方窗口"
    _cm5.get_provider_list = _orig_get_providers7
    _cm5.logout_provider = _orig_logout7
    # 选项 4 → UnregisterModelScreen：列表取自 get_model_list(custom_model=True)，
    # Enter 选中项经 core.client.logout_model(provider, model) 注销后关窗回菜单
    _models8 = [("smoke-model", "smoke-provider"), ("other-model", "other-provider")]
    _calls8 = []
    _orig_get_models8 = _cm5.get_model_list
    _orig_logout_model8 = _cm5.logout_model
    _cm5.get_model_list = lambda custom_model=False: list(_models8)

    def _fake_logout_model8(p, m):
        _calls8.append((p, m))
        _models8.remove((m, p))
        return True

    _cm5.logout_model = _fake_logout_model8
    await pilot.press("down")  # 高亮从 3 移到 4. Unregister custom model
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], UnregisterModelScreen), \
        "选项 4 未弹出模型注销列表"
    mlist = app.screen_stack[-1].query_one("#unreg-model-list", OptionList)
    assert [mlist.get_option_at_index(i).prompt.plain for i in range(mlist.option_count)] == \
        ["smoke-model [smoke-provider]", "other-model [other-provider]"], \
        "注销列表未来自 get_model_list(custom_model=True)"
    await pilot.press("enter")  # Enter 不再直接注销（统一 Delete 键）
    await settle(pilot)
    assert _calls8 == [] and app._exception is None, f"Enter 不应直接注销: {_calls8}"
    await pilot.press("delete")  # 高亮首项 smoke-model [smoke-provider] → 窗内红字原地确认
    await settle(pilot)
    mhint = app.screen_stack[-1].query_one("#confirm-hint", Static)
    mmsg = mhint.render()
    assert 'Unregister "smoke-model" from smoke-provider?' in str(mmsg) \
        and "rgb(248,113,113)" in str(mmsg.spans[0].style), \
        f"注销模型未就地显示红色确认: {mmsg!r}"
    await pilot.press("escape")  # 撤销确认：不注销
    await settle(pilot)
    assert _calls8 == [] and isinstance(app.screen_stack[-1], UnregisterModelScreen), \
        f"取消确认不应注销: {_calls8}"
    # 注销窗口本身也需 Esc 退出（同上回归）
    await pilot.press("escape")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], LoginScreen), "Esc 未关闭注销模型窗口"
    await pilot.press("enter")  # 重新打开注销列表（LoginScreen 高亮仍在 4）
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], UnregisterModelScreen)
    mlist = app.screen_stack[-1].query_one("#unreg-model-list", OptionList)  # 重开后是新实例
    await pilot.press("delete")  # 高亮首项 smoke-model [smoke-provider] → 就地确认
    await settle(pilot)
    await pilot.press("enter")  # 确认注销
    await settle(pilot)
    assert _calls8 == [("smoke-provider", "smoke-model")], \
        f"Enter 未按行内容调用 logout_model: {_calls8}"
    assert isinstance(app.screen_stack[-1], UnregisterModelScreen), \
        "注销后不应关闭注销窗口"
    assert [mlist.get_option_at_index(i).prompt.plain for i in range(mlist.option_count)] == \
        ["other-model [other-provider]"], "注销后列表应刷新"
    await pilot.press("escape")  # 手动关窗回菜单
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], LoginScreen), "Esc 未关闭注销模型窗口"
    _cm5.get_model_list = _orig_get_models8
    _cm5.logout_model = _orig_logout_model8
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], LoginScreen), "Esc 未关闭 LoginScreen"
    print("[smoke] /login OK: 4-item menu; options register/unregister, destructive ones confirm-first")
