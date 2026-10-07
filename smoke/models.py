"""冒烟自检 · /model 与 /effort：模型切换 / 当前模型标记 / 档位 Tabs 与色带。"""
from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Input, OptionList, Tab, Tabs

from core.tui.screens import EffortScreen, ModelPickerScreen
from smoke._util import prompt_of, settle


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # /model：ModelPickerScreen（OptionList 弹窗，同会话弹窗组件）行 = 模型名 + 暗灰
    # [Provider Name]；Enter 经 shared_model_client().set_model_client 切换当前模型并关窗
    # （thinking_level 不参与，走默认）；Esc 取消不触发切换；取数失败（无 openai）提示卡
    app.push_screen(ModelPickerScreen([("deepseek-chat", "deepseek"),
                                       ("qwen-max", "qwen")]))
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], ModelPickerScreen)
    ml = app.screen_stack[-1].query_one("#model-list", OptionList)
    ml_rows = [ml.get_option_at_index(i).prompt.plain for i in range(ml.option_count)]
    assert ml_rows == ["deepseek-chat [deepseek]", "qwen-max [qwen]"], ml_rows
    # 行内两段 span：模型名亮色 #f8fafc、[Provider] 段暗灰 #64748b（原始 hex）
    for i in range(ml.option_count):
        spans = ml.get_option_at_index(i).prompt.spans
        assert "#f8fafc" in str(spans[0].style), f"模型名色错误: {spans[0].style}"
        assert "#64748b" in str(spans[-1].style), f"提供商暗灰色错误: {spans[-1].style}"
    # ↑/↓ 首尾循环：首行 ↑ 跳末行、末行 ↓ 回首行（OptionList 原生循环，同 provider 弹窗）
    assert ml.highlighted == 0
    await pilot.press("up")
    assert ml.highlighted == ml.option_count - 1, f"首行按 ↑ 应跳末行: {ml.highlighted}"
    await pilot.press("down")
    assert ml.highlighted == 0, f"末行按 ↓ 应回首行: {ml.highlighted}"
    # 顶部搜索栏：按模型名子串过滤（忽略大小写）；清空恢复；仅下边框 + 默认聚焦
    m_search = app.screen_stack[-1].query_one("#search-input", Input)
    assert m_search.placeholder == "Search models…" and m_search.has_focus, m_search.placeholder
    m_row = app.screen_stack[-1].query_one("#search-row")
    assert not m_row.styles.border_top[0] and m_row.styles.border_bottom[0] == "solid"
    m_search.value = "QWEN"
    await settle(pilot)
    assert [ml.get_option_at_index(i).prompt.plain for i in range(ml.option_count)] == \
        ["qwen-max [qwen]"], "搜索应忽略大小写并只留匹配行"
    m_search.value = ""
    await settle(pilot)
    assert ml.option_count == 2, "清空搜索应恢复全部模型"
    try:
        from core.client import shared_model_client  # noqa: F401
    except Exception:
        pass
    else:
        import core.client as _cm
        _calls = []

        class _FakeClient:  # 拦 shared_model_client：记录 set_model_client 调用
            def set_model_client(self, provider, model, thinking_level="max"):
                _calls.append((provider, model, thinking_level))

        _fake = _FakeClient()
        _orig_shared = _cm.shared_model_client
        _cm.shared_model_client = lambda: _fake
        await pilot.press("enter")  # Enter 选中首行 → 切换模型并关窗
        await settle(pilot)
        assert _calls == [("deepseek", "deepseek-chat", "max")], \
            f"Enter 未配置选中模型: {_calls}"
        assert not isinstance(app.screen_stack[-1], ModelPickerScreen), \
            "切换成功后弹窗应关闭"
        print("[smoke] /model Enter OK: model switched via set_model_client")
        # 过滤后 Enter：应选中过滤结果对应的原始行（选项 id 回查，而非列表位置）
        app.push_screen(ModelPickerScreen([("deepseek-chat", "deepseek"),
                                           ("qwen-max", "qwen")]))
        await settle(pilot)
        app.screen_stack[-1].query_one("#search-input", Input).value = "QWEN"
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert _calls == [("deepseek", "deepseek-chat", "max"),
                          ("qwen", "qwen-max", "max")], f"过滤后 Enter 选错模型: {_calls}"
        print("[smoke] /model filtered Enter OK: picks the row behind the option id")
        app.push_screen(ModelPickerScreen([("qwen-max", "qwen")]))
        await settle(pilot)
        await pilot.press("escape")  # Esc 取消：不触发切换
        await settle(pilot)
        assert _calls == [("deepseek", "deepseek-chat", "max"),
                          ("qwen", "qwen-max", "max")], \
            f"Esc 取消不应触发切换: {_calls}"
        print("[smoke] /model Esc cancels without switching")
        # 当前模型标记：行内 '(current model)' 暗绿（同会话列表 (current session) 标记色）
        _fake.current_provider, _fake.current_model = "deepseek", "deepseek-chat"
        app.push_screen(ModelPickerScreen([("deepseek-chat", "deepseek")]))
        await settle(pilot)
        ml = app.screen_stack[-1].query_one("#model-list", OptionList)
        opt = ml.get_option_at_index(0)
        row_txt = opt.prompt.plain
        assert row_txt == "deepseek-chat [deepseek]  (current model)", row_txt
        assert "#34d399" in str(opt.prompt.spans[-1].style), \
            f"当前模型标记色错误（应同会话标记 #34d399）: {opt.prompt.spans[-1].style}"
        print("[smoke] /model current-model marker OK: '(current model)' in green")
        _cm.shared_model_client = _orig_shared  # 复原
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], ModelPickerScreen), "Esc 未关闭 ModelPickerScreen"
    prompt.text = "/model"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/model 不应触发 agent 回合"
    assert app._exception is None, f"渲染异常: {app._exception}"
    if isinstance(app.screen_stack[-1], ModelPickerScreen):  # 真实数据源可用：能打开弹窗
        await pilot.press("escape")
        await settle(pilot)
    else:  # 数据源不可用（演示环境）/无已配置提供商：应给出 Model List 提示卡
        bodies = [str(w.render()) for w in app.query(".card-body")]
        assert any("Model List" in b for b in bodies), bodies
    print("[smoke] /model OK: model + dim [Provider] rows in OptionList, Enter switches")

    # /effort：EffortScreen（Tabs 弹窗）标签从左到右 minimal/low/medium/high/max；
    # 当前档位标签点亮（active）；←/→ 切换（原生循环）；Enter 经
    # shared_model_client().set_thinking_level 生效并关窗；Esc 取消不生效；
    # core.model 不可用（无 openai）时提示卡而非崩溃
    app.push_screen(EffortScreen("medium"))
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], EffortScreen)
    et = app.screen_stack[-1].query_one("#effort-tabs", Tabs)
    tab_ids = [t.id for t in et.query("#tabs-list > Tab")]
    assert tab_ids == ["minimal", "low", "medium", "high", "max"], tab_ids
    assert [t.label_text for t in et.query("#tabs-list > Tab")] == tab_ids
    # 弹窗比通用 .picker（60%×60%）小：定宽 46 = 内容最宽行（提示行）40 + padding 4 + border 2
    # （app.css #effort-picker 常量；改文案须同步该常量）；高度随内容收缩
    et_picker = app.screen_stack[-1].query_one("#effort-picker", Vertical)
    assert et_picker.region.width == 46, \
        f"effort 弹窗宽度应定宽 46: {et_picker.region.width}"
    assert et_picker.region.height < app.size.height * 0.6, \
        f"effort 弹窗高度未收缩: {et_picker.region.height}"
    # 档位色带：冷→暖（灰/绿/黄/橙/红）随思考深度低→高，各标签颜色一一对应
    for tab_id, rgb in (("minimal", (148, 163, 184)), ("low", (74, 222, 128)),
                        ("medium", (250, 204, 21)), ("high", (251, 146, 60)),
                        ("max", (248, 113, 113))):
        tab_color = et.query_one(f"#tabs-list > #{tab_id}", Tab).styles.color
        assert tab_color.rgb == rgb, f"{tab_id} 档位色错误: {tab_color}"
    assert et.active == "medium", f"当前档位标签未点亮: {et.active}"
    # 滑块（Underline 高亮段）随活动档位同色：当前 medium → 黄 #facc15
    await settle(pilot)
    ul = app.screen_stack[-1].query_one("#effort-tabs Underline")

    def _bar_rgb():
        t = ul.get_component_rich_style("underline--bar").color.triplet
        return (t.red, t.green, t.blue)

    assert _bar_rgb() == (250, 204, 21), \
        f"滑块颜色未随 medium 档位: {ul.get_component_rich_style('underline--bar').color}"
    # 内容居中：标签排 / 提示行在窗内同轴居中（各自宽度不一但中心对齐）；标题已嵌上边框
    # 左端（app.css .picker border-title），不再参与居中
    await settle(pilot, 20, 0.05)
    inner = et_picker.content_region
    for child in (et, et_picker.query_one(".picker-hint")):
        center_x = child.region.x + child.region.width // 2
        inner_center = inner.x + inner.width // 2
        assert abs(center_x - inner_center) <= 1, \
            f"内容未居中: {child.region} vs 内区 {inner}"
    # 滑块即时性：原生 Tabs 切档带 0.3s 滑行动画，文字高亮先变、滑块滞后半拍
    # （_InstantTabs 已强制关动画）；切档后只给 1 帧，滑块区间须已等于目标档列区间
    low_tab = et.query_one("#tabs-list > #low", Tab)
    low_span = low_tab.virtual_region.shrink(low_tab.styles.gutter).column_span
    await pilot.press("left")
    assert et.active == "low", f"← 应切到左侧档位: {et.active}"
    await pilot.pause(0.06)
    assert (ul.highlight_start, ul.highlight_end) == low_span, \
        f"滑块未即时到位: {(ul.highlight_start, ul.highlight_end)} != {low_span}"
    await pilot.press("right")
    await pilot.press("right")
    assert et.active == "high", f"→ 应切到右侧档位: {et.active}"
    await pilot.press("right")
    assert et.active == "max", f"→ 应切到最右档位: {et.active}"
    await pilot.press("right")
    assert et.active == "minimal", f"末位 → 应循环回首档: {et.active}"
    await settle(pilot)  # 滑块色随 TabActivated 消息异步同步
    assert _bar_rgb() == (148, 163, 184), \
        f"滑块颜色未随 minimal 档位: {ul.get_component_rich_style('underline--bar').color}"
    try:
        from core.client import shared_model_client  # noqa: F401
    except Exception:
        pass
    else:
        import core.client as _cm4
        _calls4 = []

        class _FakeEffortClient:  # 拦 shared_model_client：记录 set_thinking_level 调用
            def set_thinking_level(self, level):
                _calls4.append(level)

        _orig_shared4 = _cm4.shared_model_client
        _cm4.shared_model_client = lambda: _FakeEffortClient()
        await pilot.press("right")  # minimal → low
        await pilot.press("right")  # low → medium
        await pilot.press("enter")  # Enter 应用当前档位（medium）
        await settle(pilot)
        assert _calls4 == ["medium"], f"Enter 未设置选中档位: {_calls4}"
        assert not isinstance(app.screen_stack[-1], EffortScreen), \
            "设置成功后弹窗应关闭"
        print("[smoke] /effort Enter OK: set_thinking_level applied for active tab")
        app.push_screen(EffortScreen("max"))
        await settle(pilot)
        await pilot.press("escape")  # Esc 取消：不生效
        await settle(pilot)
        assert _calls4 == ["medium"], f"Esc 取消不应设置档位: {_calls4}"
        print("[smoke] /effort Esc cancels without applying")
        _cm4.shared_model_client = _orig_shared4  # 复原
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], EffortScreen), "Esc 未关闭 EffortScreen"
    prompt.text = "/effort"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/effort 不应触发 agent 回合"
    assert app._exception is None, f"渲染异常: {app._exception}"
    if isinstance(app.screen_stack[-1], EffortScreen):  # 真实数据源可用：能打开弹窗
        await pilot.press("escape")
        await settle(pilot)
    else:  # 数据源不可用（演示环境无 openai）：应给出 Effort 提示卡
        bodies = [str(w.render()) for w in app.query(".card-body")]
        assert any("Effort" in b for b in bodies), bodies
    print("[smoke] /effort OK: tabs minimal→max, Enter applies via set_thinking_level")
