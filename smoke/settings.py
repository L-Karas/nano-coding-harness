"""冒烟自检 · /settings：AgentConfig 表单预填 / 两段式 Enter（先编辑后保存）/ 开关 Enter 切换落盘 /
非法值留在窗内 / Esc 关窗。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import core.config as config_mod
from textual.widgets import Input, Select, Static, Switch

from core.tui.screens import SettingsScreen
from smoke._util import prompt_of, settle, type_query


async def run(app, pilot) -> None:
    prompt = prompt_of(app)
    # /settings 在 / 指令候选表；Enter 应用候选并提交 → 打开设置窗
    await type_query(prompt, pilot, "/sett")
    assert prompt._candidates == ["/settings"], prompt._candidates
    with tempfile.TemporaryDirectory() as tmp:  # 隔离：冒烟不写真实 .harness/.settings.json
        setting_file = Path(tmp) / ".settings.json"
        orig = config_mod.HARNESS_SETTING_FILE
        orig_cfg = config_mod.CONFIGMANAGER.config
        config_mod.HARNESS_SETTING_FILE = setting_file
        import core.client as _client
        orig_models = _client.get_model_list
        _client.get_model_list = lambda custom_model=False: [("deepseek-chat", "deepseek"),
                                                             ("qwen-max", "qwen")]
        # 预置旧配置：default_provider 字段已删除（预置值保存后不落盘）、max_retries 不在表单 → 保存后应原样保留
        setting_file.write_text(json.dumps({"default_provider": "legacy-provider",
                                            "default_fallback_model": "legacy-model",
                                            "default_sub_model": "deepseek-chat",
                                            "max_retries": 7}), encoding="utf-8")
        try:
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen_stack[-1], SettingsScreen), "Enter /settings 未打开设置窗"
            form = app.screen_stack[-1]
            # 预填当前配置（缺省键用 AgentConfig 默认值）；模型下拉选项来自 get_model_list
            msel = form.query_one("#set-default_model", Select)
            assert form.query_one("#set-default_thinking_level", Select).value == "max"
            assert form.query_one("#set-default_sub_model_thinking_level", Select).value == "max"
            assert form.query_one("#set-approval_mode", Select).value == "Manual"
            # 缺省键取 AgentConfig 当前默认值（不写死，避免默认值调整后冒烟失真）
            assert form.query_one("#set-default_max_tokens", Input).value == \
                str(config_mod.AgentConfig.model_fields["default_max_tokens"].default)
            assert form.query_one("#set-compact_trigger_tokens", Input).value == \
                str(config_mod.AgentConfig.model_fields["compact_trigger_tokens"].default)
            assert form.query_one("#set-auto_compact", Switch).value is True
            # 未设置：空值 → 占位显示 prompt 提示（选项表首项为占位行，值为 Select.NULL）
            assert msel.is_blank() and msel.prompt == "Select a model", (msel.value, msel.prompt)
            labels = [p.plain if hasattr(p, "plain") else p for p, _value in msel._options]
            assert labels == ["", "deepseek-chat [deepseek]", "qwen-max [qwen]"], labels
            # 配置里的值不在模型表：附加原值选项（无括注）并选中，保存不丢
            fsel = form.query_one("#set-default_fallback_model", Select)
            assert fsel.value == ("", "legacy-model"), fsel.value
            flabels = [p.plain if hasattr(p, "plain") else p for p, _value in fsel._options]
            assert flabels[-1] == "legacy-model", flabels
            # 裸模型名旧值 → 解析到带提供方标注的选项（显示 [provider]），保存时迁移为 provider:model
            ssel = form.query_one("#set-default_sub_model", Select)
            assert ssel.value == ("deepseek", "deepseek-chat"), ssel.value
            slabels = [p.plain if hasattr(p, "plain") else p for p, _value in ssel._options]
            assert "deepseek-chat [deepseek]" in slabels
            # 键盘导航：未展开时 ↑/↓ 切字段；Enter 才展开浮层，Esc 只关浮层
            assert form.focused is msel, "默认聚焦第一个模型下拉"
            await pilot.press("down")
            await settle(pilot)
            assert not msel.expanded and form.focused.id == "set-default_sub_model", \
                f"↓ 应切到下一字段而非展开下拉: {form.focused.id}"
            await pilot.press("up")
            await settle(pilot)
            assert form.focused is msel, "↑ 应切回上一字段"
            await pilot.press("enter")
            await settle(pilot)
            assert msel.expanded, "Enter 应展开下拉浮层"
            # 选中背景与 /model 窗口选中态一致（app.css 同一组规则：#334155）
            hl = msel.query_one("SelectOverlay").get_component_styles("option-list--option-highlighted")
            assert hl.background.hex.lower() == "#334155", hl.background
            # 浮层滑块同 /sessions 列表：1 列宽、轨道透明、滑块 #475569（app.css 同一组规则）
            ov = msel.query_one("SelectOverlay")
            assert ov.styles.scrollbar_size_vertical == 1, ov.styles.scrollbar_size_vertical
            assert ov.styles.scrollbar_color.hex.lower() == "#475569", ov.styles.scrollbar_color
            assert ov.styles.scrollbar_background.is_transparent, ov.styles.scrollbar_background
            # 窄终端下参数名吃掉整行，Select 仅剩几列；浮层须保底可用宽
            # （回归：宽度缩到内容宽 0 时 OptionList 在 rich divide_line 崩溃）
            assert ov.region.width >= 24, f"浮层过窄: {ov.region}"
            # 设置表单主体（超长时滚动）滑块同 /sessions 列表：共用同一组滑块规则
            body = form.query_one("#settings-form")
            assert body.show_vertical_scrollbar, "设置表单超长时应有纵向滑块"
            assert body.styles.scrollbar_size_vertical == 1, body.styles.scrollbar_size_vertical
            assert body.styles.scrollbar_color.hex.lower() == "#475569", body.styles.scrollbar_color
            assert body.styles.scrollbar_background.is_transparent, body.styles.scrollbar_background
            assert body.styles.scrollbar_color_hover.hex.lower() == "#94a3b8"
            assert body.styles.scrollbar_color_active.hex.lower() == "#38bdf8"
            await pilot.press("escape")
            await settle(pilot)
            assert not msel.expanded and isinstance(app.screen_stack[-1], SettingsScreen), \
                "Esc 应只关浮层、不关窗"
            # 提示行在滚动主体之外、固定窗口底部（同 /sessions）：滚动时不随内容移出视野
            hint = form.query_one("#settings-picker > .picker-hint", Static)
            top_hint_y = hint.region.y
            assert body.region.bottom <= hint.region.y, (body.region, hint.region)
            body.scroll_end(animate=False)
            await settle(pilot)
            assert hint.region.y == top_hint_y, "提示行应固定在窗口底部，不随表单滚动"
            body.scroll_home(animate=False)
            await settle(pilot)
            rows = list(form.query(".field-row"))
            switches = list(form.query(".switch-row"))
            assert len(rows) == 11 and len(switches) == 4, \
                f"设置表单应为 11 个字段行 + 4 个开关行: {len(rows)} / {len(switches)}"
            # 字段行无下划线（高 1 行），输入控件自身也无边框
            assert all(not r.styles.border_top[0] and not r.styles.border_bottom[0]
                       and r.styles.height.value == 1 for r in rows), "字段行不应有下划线"
            # 每行左侧显示人类可读参数名（与开关行标签同一展示风格，而非字段名）
            field_labels = list(form.query(".field-label"))
            labels = [str(w.render()) for w in field_labels]
            assert labels == ["Default model", "Default sub model", "Default fallback model",
                              "Default thinking level", "Sub model thinking level",
                              "Approval mode",
                              "Default max tokens", "Escalated max tokens",
                              "Summary max tokens", "Compact trigger tokens",
                              "Compact reserve tokens"], labels
            # 字段标签与开关标签同色（此时焦点在 default_model 行，末行标签必为非聚焦态）
            switch_label = form.query_one(".switch-label", Static)
            assert field_labels[-1].styles.color == switch_label.styles.color, \
                (field_labels[-1].styles.color, switch_label.styles.color)
            # 参数名与开关名左对齐；控件右缘与开关右缘对齐（行内边距一致）
            # 字段左侧 = 主体内容区左缘（主体 padding 1 同 OptionList 默认内边距）→ 与
            # /sessions、/skills 列表项左缘一致
            assert field_labels[0].region.x == body.region.x + int(body.styles.padding.left), \
                (field_labels[0].region, body.region)
            assert field_labels[-1].region.x == switch_label.region.x, \
                (field_labels[-1].region.x, switch_label.region.x)
            input_widget = form.query_one("#set-default_max_tokens", Input)
            switch_widget = form.query_one("#set-auto_compact", Switch)
            assert (input_widget.region.x + input_widget.region.width
                    == switch_widget.region.x + switch_widget.region.width), \
                (input_widget.region, switch_widget.region)
            assert all(not i.styles.border_top[0] and not i.styles.border_left[0]
                       for i in form.query("#settings-picker Input")), "输入框应无自身边框"
            # 开关同注册模型窗：去边框 + 透明底（否则 tall 边框变厚、滑块加粗）
            assert all(not s.styles.border_top[0] and s.styles.background.a == 0
                       for s in form.query("#settings-picker Switch")), "开关应为无边框透明底"
            # thinking level 下拉浮层：圆角边框 + ansi_default 底（同注册模型窗提供方下拉）
            overlay = form.query_one("#set-default_thinking_level > SelectOverlay")
            assert str(overlay.styles.border_top[0]) == "round", "下拉浮层应为圆弧边框"
            assert overlay.styles.background.ansi == -1, \
                f"下拉浮层底色应为 ansi_default: {overlay.styles.background}"
            # 两段式 Enter：浏览态输入框锁定（打字不生效），第一次 Enter 进入编辑态后才可输入
            token_input = form.query_one("#set-default_max_tokens", Input)
            token_input.focus()
            await pilot.press("1")
            await settle(pilot)
            assert token_input.value == "16000", f"浏览态输入框应锁定: {token_input.value}"
            # 视觉区分：锁定态值暗、无 -editing、无全选高亮、光标不可见
            assert token_input.styles.color.hex.lower() == "#94a3b8", token_input.styles.color
            assert not token_input.has_class("-editing") and token_input.selection.is_empty
            cursor = token_input.get_component_styles("input--cursor")
            assert cursor.background.a == 0, "锁定态光标应不可见"
            await pilot.press("tab")  # Tab 保留：仍能切字段
            await settle(pilot)
            assert form.focused.id == "set-escalated_max_tokens", form.focused.id
            await pilot.press("shift+tab")
            await settle(pilot)
            assert form.focused is token_input, form.focused
            await pilot.press("enter")  # 第一次 Enter：进入编辑态（全选当前值）
            await settle(pilot)
            # 编辑态视觉：-editing + 整行高亮 + 亮字 + 全选 + 可见光标
            assert token_input.has_class("-editing") and not token_input.selection.is_empty
            assert token_input.styles.background.hex.lower() == "#334155", token_input.styles.background
            cursor = token_input.get_component_styles("input--cursor")
            assert cursor.background.hex.lower() == "#f8fafc", cursor.background
            await pilot.press(
                "1",
                "6",
                "0",
                "0",
                "0",
            )  # 全选状态下直接输入替换
            await settle(pilot)
            assert token_input.value == "16000", token_input.value
            await pilot.press("enter")  # 编辑态第二次 Enter：保存
            await settle(pilot)
            assert token_input.value == "16000" and not token_input.has_class("-editing"), \
                token_input.value
            # 未按 Enter 保存就切走：输入回退到编辑前值（16000 → 999 未保存 → 回 16000）
            token_input.focus()
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("9", "9", "9")
            await settle(pilot)
            assert token_input.value == "999", token_input.value
            msel.focus()
            await settle(pilot)
            assert token_input.value == "16000", "未保存的输入应在移开焦点时回退"
            assert not token_input.has_class("-editing") and token_input.selection.is_empty, \
                "移开焦点后应回到锁定态外观"
            # 下拉：第一次 Enter 进入编辑态（展开）→ ↓ 选项 → Enter 确认选中
            msel.focus()
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert msel.expanded, "第一次 Enter 应展开下拉浮层"
            await pilot.press("down", "down")  # 首行为空白占位；两下到 qwen-max
            await pilot.press("enter")
            await settle(pilot)
            assert msel.value == ("qwen", "qwen-max"), msel.value
            # 审批模式下拉：默认 Manual，浮动层 ↓ 一项到 Yolo，确认后即落盘
            amode = form.query_one("#set-approval_mode", Select)
            amode.focus()
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert amode.expanded, "Enter 应展开审批模式下拉"
            await pilot.press("down", "enter")
            await settle(pilot)
            assert amode.value == "Yolo", amode.value
            # 浮层里确认选项的 Enter 就是第二次 Enter：确认 + 立即落盘（含此前输入框编辑的 max_tokens）
            saved = json.loads(setting_file.read_text(encoding="utf-8"))
            assert saved["approval_mode"] == "Yolo", "审批模式下拉确认后应落盘"
            assert saved["default_model"] == "qwen:qwen-max", saved
            assert saved["default_sub_model_thinking_level"] == "max", saved
            assert saved["default_max_tokens"] == 16000, saved
            fsel.clear()  # 清空 fallback（空白 → 存空串），随下一次保存落盘
            # 开关除外：空格不生效；Enter 直接切换并保存（落盘一次到位）
            auto = form.query_one("#set-auto_compact", Switch)
            auto.focus()
            await settle(pilot)
            await pilot.press("space")
            await settle(pilot)
            assert auto.value is True, "空格不应切换开关"
            await pilot.press("enter")
            await settle(pilot)
            assert auto.value is False, "Enter 应直接切换开关"
            saved = json.loads(setting_file.read_text(encoding="utf-8"))
            assert saved["auto_compact"] is False, saved
            assert saved["default_max_tokens"] == 16000, saved
            # 模型字段存 'provider:model'；裸模型名旧值保存时迁移为带 provider
            assert saved["default_model"] == "qwen:qwen-max", saved
            assert saved["default_sub_model"] == "deepseek:deepseek-chat", saved
            assert saved["default_fallback_model"] == "", saved  # (not set) → 空串
            # default_provider 字段已删除（预置值不落盘）；表单外参数原样保留（max_retries）
            assert "default_provider" not in saved and saved["max_retries"] == 7, saved
            assert config_mod.AgentConfigManager.load_config().config.default_max_tokens == 16000
            assert config_mod.CONFIGMANAGER.config.default_max_tokens == 16000, "保存后运行时单例未刷新"
            assert isinstance(app.screen_stack[-1], SettingsScreen), "保存后不应关窗"
            # 鼠标：点开关直接切换并落盘
            exp = form.query_one("#set-enable_experimental_tools", Switch)
            exp.scroll_visible()
            await settle(pilot)
            before = exp.value
            await pilot.click("#set-enable_experimental_tools")
            await settle(pilot)
            assert exp.value is not before, "单击开关应切换"
            saved = json.loads(setting_file.read_text(encoding="utf-8"))
            assert saved["enable_experimental_tools"] is exp.value, "单击开关应落盘"
            # 鼠标：点锁定输入框进入编辑态（全选），输入后 Enter 落盘
            summary = form.query_one("#set-summary_max_tokens", Input)
            summary.scroll_visible()
            await settle(pilot)
            await pilot.click("#set-summary_max_tokens")
            await settle(pilot)
            assert summary.has_class("-editing"), "单击锁定输入框应进入编辑态"
            await pilot.press("9", "9", "9")  # 全选后直接输入替换
            await pilot.press("enter")
            await settle(pilot)
            assert json.loads(setting_file.read_text(encoding="utf-8"))["summary_max_tokens"] == 999, \
                "鼠标编辑后 Enter 应落盘"
            assert isinstance(app.screen_stack[-1], SettingsScreen), "保存后不应关窗"
            saved = json.loads(setting_file.read_text(encoding="utf-8"))  # 非法值断言以当前盘面为基准
            # 非法值（compact_trigger_tokens=0 违反 gt=0）：第二次 Enter 保存失败，留在窗内且不写盘
            compact = form.query_one("#set-compact_trigger_tokens", Input)
            compact.value = "0"
            compact.focus()
            await pilot.press("enter")  # 进入编辑态
            await pilot.press("enter")  # 保存 → 非法
            await settle(pilot)
            assert json.loads(setting_file.read_text(encoding="utf-8")) == saved, "非法值不应写盘"
            assert isinstance(app.screen_stack[-1], SettingsScreen), "非法值不应关窗"
            # Esc：关窗
            await pilot.press("escape")
            await settle(pilot)
            assert not isinstance(app.screen_stack[-1], SettingsScreen), "Esc 未关闭设置窗"
            print("[smoke] /settings OK: prefill, two-step Enter, Tab/↑/↓ browse, mouse click save/edit, Esc closes")
        finally:
            config_mod.HARNESS_SETTING_FILE = orig
            config_mod.CONFIGMANAGER.config = orig_cfg
            _client.get_model_list = orig_models
