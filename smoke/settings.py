"""冒烟自检 · /settings：AgentConfig 表单预填 / 两段式 Enter（先编辑后保存）/ 开关 Enter 切换落盘 /
非法值留在窗内 / Esc 关窗。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import core.config as config_mod
from textual.widgets import Input, Select, Switch

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
            # 缺省键取 AgentConfig 当前默认值（不写死，避免默认值调整后冒烟失真）
            assert form.query_one("#set-default_max_tokens", Input).value == \
                str(config_mod.AgentConfig.model_fields["default_max_tokens"].default)
            assert form.query_one("#set-compact_threshold", Input).value == \
                str(config_mod.AgentConfig.model_fields["compact_threshold"].default)
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
            await pilot.press("escape")
            await settle(pilot)
            assert not msel.expanded and isinstance(app.screen_stack[-1], SettingsScreen), \
                "Esc 应只关浮层、不关窗"
            rows = list(form.query(".field-row"))
            switches = list(form.query(".switch-row"))
            assert len(rows) == 10 and len(switches) == 4, \
                f"设置表单应为 10 个字段行 + 4 个开关行: {len(rows)} / {len(switches)}"
            # 字段行无下划线（高 1 行），输入控件自身也无边框
            assert all(not r.styles.border_top[0] and not r.styles.border_bottom[0]
                       and r.styles.height.value == 1 for r in rows), "字段行不应有下划线"
            # 每行左侧显示对应参数名
            labels = [str(w.render()) for w in form.query(".field-label")]
            assert labels == ["default_model", "default_sub_model", "default_fallback_model",
                              "default_thinking_level", "default_sub_model_thinking_level",
                              "default_max_tokens", "escalated_max_tokens",
                              "summary_max_tokens", "compact_threshold",
                              "reserve_threshold"], labels
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
            assert token_input.styles.color.hex.lower() == "#64748b", token_input.styles.color
            assert not token_input.has_class("-editing") and token_input.selection.is_empty
            cursor = token_input.get_component_styles("input--cursor")
            assert cursor.background.a == 0, "锁定态光标应不可见"
            await pilot.press("tab")  # 锁定输入框不拦 Tab：仍能切字段
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
            await pilot.press("1", "6", "0", "0", "0")  # 全选状态下直接输入替换
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
            # 浮层里确认选项的 Enter 就是第二次 Enter：确认 + 立即落盘（含此前输入框编辑的 max_tokens）
            saved = json.loads(setting_file.read_text(encoding="utf-8"))
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
            # 非法值（compact_threshold=0 违反 gt=0）：第二次 Enter 保存失败，留在窗内且不写盘
            compact = form.query_one("#set-compact_threshold", Input)
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
            print("[smoke] /settings OK: prefill, two-step Enter edit/save, switch toggle+save, Esc closes")
        finally:
            config_mod.HARNESS_SETTING_FILE = orig
            config_mod.CONFIGMANAGER.config = orig_cfg
            _client.get_model_list = orig_models
