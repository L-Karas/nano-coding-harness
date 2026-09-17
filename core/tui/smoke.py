"""ui_textual 的冒烟自检：`python -m core.tui.ui_textual --smoke`。

从 ui_textual.py 的 __main__ 整体拆出（原近 800 行堆在主模块尾部）：用 Textual
run_test 无头驱动 ChatApp，断言驱动全流程（渲染 / 流式 / 补全 / 弹窗 / 权限 /
右栏数据源 / 页脚），失败即抛 AssertionError。仅 --smoke 分支按需导入，正常运行
与接入 Agent 不加载本模块。
"""

import asyncio

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static, Tab, Tabs

import core.tui.render as _render
from core.tui.demo import _demo_agent
from core.tui.render import (
    render_tool_call,
    render_tool_result,
    render_tool_result_diff,
    render_user_input,
)
from core.tui.theme import _SPINNER_FRAMES
from core.tui.ui_textual import ChatApp
from core.tui.widgets import (
    ApiKeyScreen,
    EffortScreen,
    ModelPickerScreen,
    ProviderScreen,
    SessionPickerScreen,
    SkillsScreen,
    _CommandInput,
    _match_project_entries,
)


async def _smoke() -> None:
    app = ChatApp(handle_query=_demo_agent)
    _render._APP = app  # 冒烟不经 run()：直接把实例挂到渲染桥接全局
    try:
        async with app.run_test() as pilot:
            async def settle(pilot, times=10, delay=0.02) -> None:
                """等 UI 稳定：连做几次短 pause 让事件循环推进（异步挂载/渲染完成）"""
                for _ in range(times):
                    await pilot.pause(delay)

            async def type_query(prompt, pilot, text) -> None:
                """程序化输入并刷新补全候选（赋值不触达键入事件路径，须手动刷新）"""
                prompt.text = text
                prompt.cursor_location = (0, len(text))
                prompt._refresh_suggestions()
                await settle(pilot)

            await pilot.pause(0.2)
            prompt = app.query_one("#prompt", _CommandInput)
            left_w = app.query_one("#left").region.width  # 左栏宽（4:1 分栏后为终端 80%）
            # 标题栏动态效果：帧间 spinner/渐变应轮播（0.12s 帧，等 0.3s 必然跨帧）
            title = app.query_one("#titlebar-text", Static)
            t0 = str(title.render())
            await pilot.pause(0.3)
            assert str(title.render()) != t0, "标题动画未轮播"
            # 输入框宽度应占满左栏宽度（不随内容变化）
            assert prompt.size.width >= left_w - 10, f"输入框宽度未占满左栏: {prompt.size.width}"
            # 长文本超过终端宽度应自动换行、多行加高（height:auto）
            prompt.text = "y" * 300
            await pilot.pause(0.05)
            assert prompt.size.height >= 4, f"长文本未多行加高: {prompt.size.height}"
            prompt.text = "hello textual"
            await pilot.press("enter")
            for _ in range(300):  # 最多 ~15s 等演示回合结束
                await pilot.pause(0.05)
                if not app._busy:
                    break
            assert not app._busy, "回合未结束"
            render_tool_result("line\n" * 15)  # 触发截断提示（含方括号），回归渲染期 MissingStyle
            render_tool_call("bash", list(range(15)))  # 17 行参数 JSON → 同样折叠为 10 行 + 1 行提示
            await settle(pilot, 10, 0.05)
            assert app._exception is None, f"渲染异常: {app._exception}"
            bodies = [str(w.render()) for w in app.query(".card-body")]
            assert any("... [truncated 5 lines]" in b for b in bodies), \
                "工具结果 15 行应折叠为 10 行正文 + 1 行提示"
            assert any("... [truncated 7 lines]" in b for b in bodies), \
                "工具调用 17 行参数应折叠为 10 行正文 + 1 行提示"
            # 截断卡片点击展开：真实鼠标点击折叠的 diff 卡末行提示 → 全文（含第 11~15 行）；
            # 再点展开态末行（折叠提示）收回截断
            render_tool_result_diff([("+", i, f"add {i}") for i in range(1, 16)])
            await settle(pilot, 10, 0.05)
            assert app.query("#chat .card.diff"), "diff 预览未渲染为 diff 卡"
            exp_bodies = [w for w in app.query(".card-body") if w.has_class("-expandable")]
            assert len(exp_bodies) == 3, f"应 3 张可展开卡片: {len(exp_bodies)}"
            diff_w = exp_bodies[-1]
            await pilot.click(diff_w, offset=(2, diff_w.region.height - 1))  # 末行=提示行
            await settle(pilot, 5)
            diff_str = str(diff_w.render())
            assert "+15 │ add 15" in diff_str and "· click to collapse" in diff_str, "点击未展开 diff 全文"
            await pilot.click(diff_w, offset=(2, diff_w.region.height - 1))
            await settle(pilot, 5)
            assert "... [truncated 5 lines]" in str(diff_w.render()), "再点未收回截断"
            # 结果/调用卡：同一展开逻辑（exp_bodies 按挂载序 = result / tool / diff）
            for w, tail in ((exp_bodies[0], None), (exp_bodies[1], "  14")):
                app._toggle_expand(w)
                full = str(w.render())
                if tail is None:
                    assert full.count("line\n") == 15, f"展开应显示全部 15 行: {full!r}"
                else:
                    assert tail in full, f"展开应显示被折叠的参数尾部: {full!r}"
                assert "· click to collapse" in full
                app._toggle_expand(w)
                collapsed = str(w.render())
                assert "... [truncated " in collapsed, "收回后应回到截断态"
            print("[smoke] expandable tool cards OK: click expands/collapses full content")
            # 工具失败输出（约定前缀）→ 暗红 error 卡；普通输出仍为 result 卡
            render_tool_result("[Tool Error]: disk quota exceeded")
            await settle(pilot, 5)
            err_bodies = app.query("#chat .card.error .card-body")
            assert err_bodies and "[Tool Error]" in str(err_bodies.last().render()), \
                "工具失败输出未渲染为暗红 error 卡"
            render_tool_result("ordinary tool output")  # 对照：非失败输出仍为 result 卡
            await settle(pilot, 5)
            assert list(app.query("#chat .card"))[-1].has_class("result"), "普通工具输出误判为 error"
            print("[smoke] tool error card OK: [Tool Error]: prefix renders dark-red card")
            cards = list(app.query("#chat .card"))
            assert len(cards) >= 5, f"卡片数量不足: {len(cards)}"
            # 消息卡片宽度 = 左栏 - 1（内容超出视口时右侧滑块占 1 列）；无溢出时仍占满左栏
            assert left_w - 2 <= cards[0].region.width <= left_w, \
                f"卡片宽度异常: {cards[0].region.width} (左栏 {left_w})"
            assert any("assistant" in c.classes for c in cards), "缺少 Assistant 卡片"
            # 增量流式自检：每 chunk 只传新增片段 → 内容逐字拼接，不丢不重
            app._stop_stream()
            parts = ["## Title\n", "First paragraph text\n\n", "```python\nprint(1)\n```\n", "end"]
            for p in parts:
                app._stream_update(p)
                await settle(pilot)
            md = list(app.query("#chat .card.assistant Markdown"))[-1]  # 本轮新卡（demo 卡在前）
            assert md.source == "".join(parts), \
                f"增量流式内容不一致: {md.source!r}"
            app._stop_stream()  # 停掉本轮 MarkdownStream 后台任务
            await settle(pilot)
            print("[smoke] markdown incremental stream OK (chunk append, no drop)")
            chat = app.query_one("#chat", VerticalScroll)
            # 卡片 height:auto 后内容超出视口才可滚动；回归 1fr 均分时 max_scroll_y 恒为 0
            assert chat.max_scroll_y >= 1, "卡片未按内容自适应高度，消息列表不可滚动"
            # 右侧滑块：内容溢出时必须可见（曾 scrollbar-size-vertical:0 隐藏）；1 列宽便于拖动拇指浏览历史
            assert chat.show_vertical_scrollbar, "内容溢出时未显示纵向滑块"
            assert chat.styles.scrollbar_size_vertical == 1, "滑块宽度应为 1 列"
            await settle(pilot, 5)
            assert chat.vertical_scrollbar.region.height >= 1, "滑块未渲染出可见区域"
            # 滚动钉底：回合结束后仍钉在底部（无用户滚动，anchor 不应解除）
            assert chat._anchored and not chat._anchor_released, \
                f"回合结束应保持钉底: anchored={chat._anchored} released={chat._anchor_released}"
            # 浏览历史：用户上滚（等价滚轮上滚 / 拖滑块）→ 解除钉底，追加内容不再拉回视口；
            # 回到底部 → 自动恢复钉底（后续流式继续跟随）
            chat.scroll_up()
            await pilot.pause(0.1)
            assert chat._anchor_released, "用户上滚后应解除钉底"
            pos = chat.scroll_y
            render_user_input("browse-mid-stream")  # 追加新卡片
            await settle(pilot, 10, 0.05)
            assert chat.scroll_y == pos, f"浏览中追加内容不应拉动视口: {chat.scroll_y} != {pos}"
            chat.scroll_end(animate=False, immediate=True)
            await pilot.pause(0.1)
            assert not chat._anchor_released, "滚回底部后应恢复钉底"
            print("[smoke] scrollbar visible + anchor browse/repin OK")
            # 指令补全：/ 前缀输入时输入框下方弹出候选 OptionList（仅此时可见），↑/↓+Tab/Enter 接受；其它输入不显示
            prompt.focus()
            suggest = app.query_one("#cmd-suggest", OptionList)
            prompt.text = "/"
            await settle(pilot)
            assert suggest.styles.display != "none", "输入 / 未弹出候选 OptionList"
            assert prompt._candidates == ["/new", "/sessions", "/compact", "/skills", "/provider", "/model",
                                          "/effort", "/exit"], prompt._candidates
            # 候选行 = 指令名列（含别名括注）+ 空距 + 简短说明（选项 prompt 为分段 Text，
            # plain 即整行字符），各行说明左端对齐于同一列
            assert [suggest.get_option_at_index(i).prompt.plain
                    for i in range(suggest.option_count)] == [
                       "/new            Start a fresh session",
                       "/sessions       Open the session picker",
                       "/compact        Compact the conversation history",
                       "/skills         List available skills",
                       "/provider       Configure API providers",
                       "/model          Switch the active model",
                       "/effort         Set the thinking effort",
                       "/exit (quit)    Quit the app"], \
                "候选行应含指令简短说明且说明列左端对齐"
            await pilot.press("down")
            await pilot.press("tab")
            assert prompt.text == "/sessions", f"Tab 接受高亮失败: {prompt.text!r}"
            await settle(pilot, 5)
            assert suggest.styles.display == "none", "完整指令后列表应隐藏"
            prompt.text = "/sk"  # /skills 前缀
            await settle(pilot)
            assert prompt._candidates == ["/skills"], prompt._candidates
            prompt.text = "/s"
            await settle(pilot)
            assert prompt._candidates == ["/sessions", "/skills"], prompt._candidates
            assert suggest.option_count == 2, f"过滤候选条数错误: {suggest.option_count}"
            await pilot.press("enter")  # Enter 应用高亮项（首项 /sessions）并提交
            await settle(pilot, 20)
            assert not app._busy, "Enter 应提交补全后的 /sessions（走会话分支，不起 agent 回合）"
            bodies = [str(w.render()) for w in app.query(".card-body")]
            assert any("SessionManager not connected" in b for b in bodies), "Enter 未提交 /sessions"
            prompt.text = "/q"  # /quit 别名前缀 → 命中 /exit 行
            await settle(pilot)
            assert prompt._candidates == ["/exit"], prompt._candidates
            assert suggest.option_count == 1 and suggest.get_option_at_index(0).prompt.plain == \
                   "/exit (quit)    Quit the app", "别名行未显示括注"
            prompt.text = "/xyz"
            await settle(pilot)
            assert suggest.styles.display == "none", "无匹配不应显示列表"
            prompt.text = "hello world"
            await settle(pilot)
            assert suggest.styles.display == "none", "普通消息不应显示列表"
            print(f"[smoke] OK, {len(cards)} cards rendered, scrollable, optionlist completion OK")

            # /provider：ProviderScreen（OptionList 弹窗，同会话弹窗组件）行 = 首字母大写
            # provider + 配置状态，未配置灰 / 已配置暗绿；遮罩半透明 → 窗外透出主界面（不整屏盖死）；
            # Esc 关闭；实际取数失败（演示环境无 openai）时提示卡而非崩溃
            app.push_screen(ProviderScreen([("deepseek", False), ("qwen", True)]))
            await settle(pilot)
            assert isinstance(app.screen_stack[-1], ProviderScreen)
            # 遮罩半透明：弹窗打开时主界面（screen 栈底层）仍参与合成，窗外可见主界面内容
            assert app.screen.styles.background.a < 1, "弹窗遮罩应半透明（窗外透出主界面）"
            pv = app.screen_stack[-1].query_one("#provider-list", OptionList)
            pv_rows = [pv.get_option_at_index(i).prompt.plain for i in range(pv.option_count)]
            assert pv_rows == ["Deepseek [○ unconfigured]", "Qwen [● configured]"], pv_rows
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
                # Delete 删除选中行配置：unconfigure_provider 落盘 + 行状态回未配置（灰）；
                # 已未配置行再按 Delete 为空操作（幂等，不落盘不报错）
                await pilot.press("delete")
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
            print("[smoke] /provider OK: capitalized name + colored config status in OptionList")

            # /model：ModelPickerScreen（OptionList 弹窗，同会话弹窗组件）行 = 模型名 + 暗灰
            # [Provider Name]；Enter 经 shared_model_client().set_model_client 切换当前模型并关窗
            # （thinking_level 不参与，走默认）；Esc 取消不触发切换；取数失败（无 openai）提示卡
            app.push_screen(ModelPickerScreen([("deepseek-chat", "[Deepseek]"),
                                               ("qwen-max", "[Qwen]")]))
            await settle(pilot)
            assert isinstance(app.screen_stack[-1], ModelPickerScreen)
            ml = app.screen_stack[-1].query_one("#model-list", OptionList)
            ml_rows = [ml.get_option_at_index(i).prompt.plain for i in range(ml.option_count)]
            assert ml_rows == ["deepseek-chat [Deepseek]", "qwen-max [Qwen]"], ml_rows
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
                app.push_screen(ModelPickerScreen([("qwen-max", "[Qwen]")]))
                await settle(pilot)
                await pilot.press("escape")  # Esc 取消：不触发切换
                await settle(pilot)
                assert _calls == [("deepseek", "deepseek-chat", "max")], \
                    f"Esc 取消不应触发切换: {_calls}"
                print("[smoke] /model Esc cancels without switching")
                # 当前模型标记：行内 '(current model)' 暗绿（同会话列表 (current session) 标记色）
                _fake.current_provider, _fake.current_model = "deepseek", "deepseek-chat"
                app.push_screen(ModelPickerScreen([("deepseek-chat", "[Deepseek]")]))
                await settle(pilot)
                ml = app.screen_stack[-1].query_one("#model-list", OptionList)
                opt = ml.get_option_at_index(0)
                row_txt = opt.prompt.plain
                assert row_txt == "deepseek-chat [Deepseek]  (current model)", row_txt
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
            # 内容居中：标题 / 标签排 / 提示行在窗内同轴居中（各自宽度不一但中心对齐）
            # 纯 CSS 承担（标题/提示 1fr 行 + #tabs-scroll align），无需等 Python 摆位落地
            await settle(pilot, 20, 0.05)
            inner = et_picker.content_region
            for child in (et_picker.query_one(".picker-title"), et, et_picker.query_one(".picker-hint")):
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

            # 页脚右端当前模型状态：'(provider) model * level' 与 cwd 同行、暗灰右对齐；
            # level 为空时不带 ' * ' 后缀；数据源 shared_model_client（/model 切换后即刷新）
            try:
                from core.client import shared_model_client  # noqa: F401
            except Exception:
                pass
            else:
                import core.client as _cm3

                class _FakeModelState:
                    def __init__(self, provider: str, model: str, level: str):
                        self.current_provider = provider
                        self.current_model = model
                        self.current_thinking_level = level

                import core.tui.ui_textual as _mod  # 钉死模块级 cwd / git 取值点（页脚右对齐断言）
                _wd, _gb = _mod.working_directory, _mod.current_git_branch
                _shared = _cm3.shared_model_client
                try:
                    _mod.working_directory = lambda: "cwd"  # 钉死左段宽度，右对齐可精确断言
                    _mod.current_git_branch = lambda: ""
                    _cm3.shared_model_client = lambda: _FakeModelState("deepseek", "deepseek-chat", "max")
                    app._refresh_footer()
                    footer = app.query_one("#footer", Static)
                    seg = "(deepseek) deepseek-chat * max"
                    row = str(footer.render())
                    assert row == "cwd" + " " * (footer.content_region.width - 3 - len(seg)) + seg, \
                        f"模型状态未右对齐到页脚右下角: {row!r}"
                    tail = footer.render().spans[-1]
                    assert "100,116,139" in str(tail.style), f"模型段应为暗灰: {tail.style}"
                    _cm3.shared_model_client = lambda: _FakeModelState("deepseek", "deepseek-chat", "")
                    app._refresh_footer()
                    row = str(footer.render())
                    assert " * " not in row, "level 为空不应带 ' * ' 后缀"
                    assert row.rstrip().endswith("(deepseek) deepseek-chat"), row
                finally:
                    _mod.working_directory, _mod.current_git_branch = _wd, _gb
                    _cm3.shared_model_client = _shared
                    app._refresh_footer()
                print("[smoke] footer model info OK: '(provider) model * level' dim, right-aligned")

            # @ 文件补全：空白/行首后的 @ 弹 OptionList 展示同级文件（目录以 / 结尾）；
            # 输入前缀匹配过滤；Tab/Enter 选中把 @完整路径 补进输入（不发送）；完整文件名后列表隐藏
            prompt.focus()
            flv = app.query_one("#file-suggest", OptionList)
            assert flv.styles.display == "none", "未输入 @ 不应显示文件列表"
            await type_query(prompt, pilot, "check @core/tu")
            expected = _match_project_entries("core/tu")
            assert prompt._file_candidates == expected and expected, prompt._file_candidates
            assert flv.styles.display != "none", "输入 @ 前缀未弹出文件列表"
            shown = [flv.get_option_at_index(i).prompt.plain
                     for i in range(flv.option_count)]
            assert shown == expected, f"列表展示与匹配不一致: {shown} != {expected}"
            assert all(c.endswith("/") for c in expected), \
                f"同级规则：只应展示目录 core/tui/，实际 {expected}"
            await pilot.press("tab")  # 选中 core/tui/ → 补全为 @core/tui/ 并展开其内容
            await settle(pilot)
            assert prompt.text == "check @core/tui/", prompt.text
            inner = _match_project_entries("core/tui/")
            assert prompt._file_candidates == inner and inner, prompt._file_candidates
            assert all(c.startswith("core/tui/") for c in inner), \
                "进入目录后应展示完整路径"
            shown = [flv.get_option_at_index(i).prompt.plain
                     for i in range(flv.option_count)]
            assert shown == inner, f"展开目录展示不一致: {shown} != {inner}"
            # 无 / 的 query：全树相似匹配（名称包含 ui），命中深层文件 → 展示完整路径
            await type_query(prompt, pilot, "view @ui")
            fuzzy = _match_project_entries("ui")
            assert prompt._file_candidates == fuzzy and len(fuzzy) >= 2, prompt._file_candidates
            assert fuzzy[0] == "core/tui/"  # 短路径在前
            assert "core/tui/ui_textual.py" in fuzzy, fuzzy
            assert all(c.rstrip("/").rsplit("/", 1)[-1].lower().find("ui") >= 0 for c in fuzzy), \
                f"相似匹配应只含名称带 ui 的条目: {fuzzy}"
            shown = [flv.get_option_at_index(i).prompt.plain
                     for i in range(flv.option_count)]
            assert shown == fuzzy, f"列表展示与相似匹配不一致: {shown} != {fuzzy}"
            # 大小写不敏感
            await type_query(prompt, pilot, "view @TUI")
            assert prompt._file_candidates == ["core/tui/"], prompt._file_candidates
            await pilot.press("enter")  # 选中目录：补全 @core/tui/ 并展开其同级内容
            await settle(pilot)
            assert prompt.text == "view @core/tui/", prompt.text
            assert prompt._file_candidates == _match_project_entries("core/tui/"), \
                prompt._file_candidates
            await type_query(prompt, pilot, "@zz_not_exists")  # 无匹配 → 列表隐藏
            assert prompt._file_candidates == [] and flv.styles.display == "none"
            # 滑块复位：滚到列表深处后隐藏再重开 @ 列表 → 滑块回到最顶端
            await type_query(prompt, pilot, "@py")
            await settle(pilot)
            assert len(prompt._file_candidates) > 10, prompt._file_candidates
            flv.scroll_end(animate=False)
            await settle(pilot)
            assert flv.scroll_y > 0, f"长列表应可滚动到底: {flv.scroll_y}"
            await type_query(prompt, pilot, "@zz_not_exists")  # 隐藏（等同删除 @ 后重输）
            await type_query(prompt, pilot, "@py")
            await settle(pilot)
            assert flv.scroll_y == 0, f"重开后滑块应回到最顶端: {flv.scroll_y}"
            # 默认选中第一项：改选后细化输入 / 隐藏重开 → 高亮复位到首行
            await pilot.press("down")
            await pilot.press("down")
            await settle(pilot)
            assert flv.highlighted == 2, "方向键应把高亮移到第三行"
            await type_query(prompt, pilot, "@p")  # 细化输入：候选整批重建
            await settle(pilot)
            assert flv.highlighted == 0, "细化重建后应默认选中第一项"
            await type_query(prompt, pilot, "@zz_no")  # 隐藏
            await type_query(prompt, pilot, "@py")  # 重开
            await settle(pilot)
            assert flv.highlighted == 0, "重开后应默认选中第一项"
            await type_query(prompt, pilot, "edit @core/tui/util")
            assert prompt._file_candidates == ["core/tui/utils.py"], prompt._file_candidates
            await pilot.press("enter")  # 选中文件：补全，不发送
            await settle(pilot)
            assert prompt.text == "edit @core/tui/utils.py", prompt.text
            assert flv.styles.display == "none", "补全为完整文件名后列表应隐藏"
            assert not app._busy, "Enter 选中 @ 文件不应提交回合"
            prompt.text = "/new"  # 复位（指令提交不触发 agent 回合）
            await pilot.press("enter")
            await settle(pilot)
            assert not app._busy and flv.styles.display == "none"
            print("[smoke] @ file completion OK: same-level list, / drill-down, full-path insert")

            # /new 延迟建会话：只清空 current_session 指针，不立即 new_session（防空会话）；
            # 忙碌中 /new 被拒绝（否则本轮后续 add_message 会把回话写进新建会话）
            class _FakeMgr:
                def __init__(self):
                    self.current_session = "session-old.jsonl"
                    self.created = 0

                def new_session(self):
                    self.created += 1
                    self.current_session = f"session-new-{self.created}.jsonl"

            fake = _FakeMgr()
            app._manager = fake
            prompt.text = "/new"
            await pilot.press("enter")
            await settle(pilot)
            assert fake.created == 0 and fake.current_session == "", \
                "/new 应立即创建会话（延迟到首条消息，由 add_message 建）"
            assert not app._busy
            app._busy = True  # 模拟回合进行中：/new 应提示并保持会话指针不动
            prompt.text = "/new"
            await pilot.press("enter")
            await settle(pilot)
            assert fake.created == 0 and fake.current_session == "", \
                "忙碌中 /new 不应清空会话指针"
            bodies = [str(w.render()) for w in app.query(".card-body")]
            assert any("Previous turn is still running" in b for b in bodies), "忙碌中 /new 应提示"
            app._busy = False
            app._manager = None
            app._clear_cards()
            print("[smoke] /new defers session creation until first message OK")

            # 会话选择弹窗：大量会话时弹窗不得超出屏幕/裁剪列表，↓ 可滚动到最后一个会话
            class _FakeSession:
                def __init__(self, i: int):
                    self.id = f"session-{i:06d}.jsonl"
                    self.title = f"Session {i}"
                    self.timestamp = f"2025-01-01 {10 + i // 60:02d}:{i % 60:02d}:{i % 60:02d}"

            class _FakeManager:
                current_session = "session-000001.jsonl"
                sessions = [_FakeSession(i) for i in range(150)]

                def load_session_list(self):
                    return list(self.sessions)

                def delete_session(self, sid):
                    self.sessions = [s for s in self.sessions if s.id != sid]

                def load_session(self, sid):
                    return next((s for s in self.sessions if s.id == sid), None)

            app.push_screen(SessionPickerScreen(_FakeManager()))
            await settle(pilot)
            scr = app.screen_stack[-1]
            olist = scr.query_one("#sess-list", OptionList)
            picker = olist.parent
            # 回归：picker 曾 height:auto+max-height 搭配 Center(height:auto)，小终端下整体溢出屏幕、
            # 列表底部被裁剪（overflow:hidden），后半段会话永远不可见/不可达
            assert scr.region.contains_region(picker.region), \
                f"弹窗超出屏幕: {picker.region} / {scr.region}"
            assert olist.region.bottom <= picker.region.bottom, \
                f"会话列表被弹窗裁剪: {olist.region} / {picker.region}"
            assert olist.option_count == 150
            # 排序：新到旧；每行时间戳顶到行最右（回归：曾无时间戳列/左对齐）
            order = sorted(_FakeManager.sessions, key=lambda s: s.timestamp, reverse=True)
            from textual.style import Style as TStyle
            for pos in range(150):
                opt = olist.get_option_at_index(pos)
                assert opt.id == order[pos].id, f"第 {pos} 行排序错误: {opt.id}"
                row = olist._get_option_render(opt, TStyle())[0]
                assert row.cell_length == olist.scrollable_content_region.width
                # 展示到分钟（去秒），秒仅用于排序
                shown = order[pos].timestamp[:16]
                assert row.text.rstrip().endswith(shown), row.text[-25:]
            for _ in range(149):
                await pilot.press("down")
            await pilot.pause(0.05)
            assert olist.highlighted == 149, f"↓ 无法到达最后一个会话: {olist.highlighted}"
            # 末行 = 最旧会话（列表新到旧排列，order[-1] 即末尾行）
            assert olist.get_option_at_index(149).id == order[-1].id
            await pilot.press("escape")
            await settle(pilot)
            assert app._exception is None, f"渲染异常: {app._exception}"
            print("[smoke] sessions picker OK: 150 sessions, all reachable, no clipping")
            # 右栏信息分区：Todos / Background Tasks 折叠列表（Skills 已迁往 /skills
            # 弹窗，不再显示在右栏）。默认展开并同步模块级数据源（core.todo CURRENT_TODOS /
            # core.background_task BACKGROUND_TASKS，1s 轮询）；标题行点击 ▼/▶ 独立
            # 折叠/展开（列表本体隐藏，标题保留）
            import core.todo.todo as _todo_mod
            import core.skill.skills as _skills_mod
            from core.background_task import BACKGROUND_LOCK, BACKGROUND_TASKS
            todos_head = app.query_one("#todos-head", Static)
            bg_head = app.query_one("#bg-head", Static)
            assert str(todos_head.render()).startswith("▼ Todos"), "Todos 分区应默认展开"
            assert str(bg_head.render()).startswith("▼ Background Tasks"), "bg 分区应默认展开"
            assert not list(app.query("#skills-head")) and not list(app.query("#skills-section")), \
                "Skills 不应再显示在右栏（已迁往 /skills 弹窗）"
            assert todos_head.region.y < bg_head.region.y, "Todos 分区应在 bg 上方"
            _skills_orig = dict(_skills_mod.SKILL_REGISTRY)  # 注入后复原，不影响进程内其它逻辑
            _skills_mod.SKILL_REGISTRY.update({
                "smoke-skill-a": {"name": "smoke-skill-a", "description": "writes smoke checks", "content": ""},
                "smoke-skill-b": {"name": "smoke-skill-b", "description": "example skill description", "content": ""},
            })
            _todo_mod.CURRENT_TODOS = [_todo_mod.Todo("add right-panel smoke checks", "in_progress"),
                                       _todo_mod.Todo("hook up the real agent", "completed")]
            with BACKGROUND_LOCK:  # 结构对齐 core/background_task.py start_background_task 的登记
                BACKGROUND_TASKS["bg-7777"] = {"tool_call_id": "t-1",
                                               "tool_call": "bash(command='pip install textual', cwd='/very/long/remote/path')",
                                               "status": "running"}
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
            assert "bg-7777" in bg_str and "pip install textual" in bg_str, bg_str
            assert any(g in bg_str for g in _SPINNER_FRAMES), f"running 任务应有轮播字形: {bg_str}"
            await pilot.pause(0.5)  # 覆盖 ≥2 次 0.1s 动画 tick
            bg_animated = _rows_text("#bg-list .info-row")
            assert bg_animated != bg_str, "running 后台任务应呈动态加载效果（帧未推进）"
            with BACKGROUND_LOCK:  # 模拟 bg 线程完成
                BACKGROUND_TASKS["bg-7777"]["status"] = "completed"
            await pilot.pause(1.3)
            bg_str = _rows_text("#bg-list .info-row")
            assert "● bg-7777" in bg_str, f"bg 完成态未上屏: {bg_str}"
            assert not any(g in bg_str for g in _SPINNER_FRAMES), f"completed 后轮播应停: {bg_str}"
            # 长调用折叠为单行摘要（截断 …），点击 bg 行展开未截断全文、再点收回
            # （列表里可能还有演示回合遗留的其它任务，目标行先滚入视口再点）
            app.query_one("#bg-list", VerticalScroll).scroll_end(animate=False, immediate=True)
            await pilot.pause(0.1)
            bg_row = next(r for r in app.query("#bg-list .info-row.-expandable")
                          if "bg-7777" in str(r.render()))
            assert "…" in str(bg_row.render()) and "very/long/remote/path" not in str(bg_row.render()), \
                "折叠摘要应截断长调用"
            await pilot.click(bg_row, offset=(2, bg_row.region.height - 1))
            await pilot.pause(0.1)
            expanded_bg = str(bg_row.render())
            assert expanded_bg.startswith("▾ ● bg-7777") and "very/long/remote/path" in expanded_bg, \
                f"展开应显示未截断调用: {expanded_bg}"
            await pilot.click(bg_row, offset=(2, bg_row.region.height - 1))
            await pilot.pause(0.1)
            assert "very/long/remote/path" not in str(bg_row.render()), "再点应收起完整调用"
            await pilot.click(bg_head)  # 折叠 bg 分区（卡片收成只包标题）
            bg_section = app.query_one("#bg-section", Vertical)
            assert bg_section.has_class("-collapsed")
            assert str(bg_head.render()).startswith("▶ Background Tasks")
            assert not app.query_one("#bg-list", VerticalScroll).display, "折叠后列表应隐藏"
            await pilot.click(bg_head)  # 再点展开
            assert not bg_section.has_class("-collapsed")
            assert str(bg_head.render()).startswith("▼ Background Tasks")
            assert app.query_one("#bg-list", VerticalScroll).display, "展开后列表应恢复"
            print("[smoke] right info sections OK: todos/bg lists live-sync + independent collapse")

            # /skills：SkillsScreen（OptionList 弹窗，同会话 /provider /model 弹窗组件）
            # 列出全部已扫描技能，行 = 技能名（天蓝）+ 描述（暗灰完整多行）；Esc 关闭；
            # 无技能时给出提示卡而非空弹窗（点击右栏条目后焦点可能已不在输入框：先显式聚焦）
            prompt.focus()
            prompt.text = "/skills"
            await pilot.press("enter")
            await settle(pilot, 10)
            assert not app._busy, "/skills 不应触发 agent 回合"
            assert app._exception is None, f"渲染异常: {app._exception}"
            assert isinstance(app.screen_stack[-1], SkillsScreen), "/skills 未打开技能弹窗"
            sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
            rows = [sk_lst.get_option_at_index(i).prompt.plain for i in range(sk_lst.option_count)]
            assert any(r.startswith("smoke-skill-a") and "writes smoke checks" in r for r in rows), rows
            assert any(r.startswith("smoke-skill-b") and "example skill description" in r for r in rows), rows
            assert any("find-skills" in r for r in rows), "真实磁盘扫描的技能也应列出"
            # 行布局：首行仅技能名，描述自第二行起（strip 后整段插入）
            a_row = next(r for r in rows if r.startswith("smoke-skill-a"))
            assert a_row.splitlines() == ["smoke-skill-a", "writes smoke checks"], a_row
            # 行内两段 span：技能名天蓝（#7dd3fc）、描述暗灰（#64748b，同右栏原配色）
            sk_opt_a = next(sk_lst.get_option_at_index(i) for i in range(sk_lst.option_count)
                            if sk_lst.get_option_at_index(i).prompt.plain.startswith("smoke-skill-a"))
            spans = sk_opt_a.prompt.spans
            assert "#7dd3fc" in str(spans[0].style), f"技能名色错误: {spans[0].style}"
            assert "#64748b" in str(spans[-1].style), f"描述色错误: {spans[-1].style}"
            # ↑/↓ 首尾循环：首行 ↑ 跳末行、末行 ↓ 回首行（OptionList 原生循环，同其它弹窗）
            assert sk_lst.highlighted == 0
            await pilot.press("up")
            assert sk_lst.highlighted == sk_lst.option_count - 1, f"首行按 ↑ 应跳末行: {sk_lst.highlighted}"
            await pilot.press("down")
            assert sk_lst.highlighted == 0, f"末行按 ↓ 应回首行: {sk_lst.highlighted}"
            await pilot.press("escape")
            await settle(pilot)
            assert not isinstance(app.screen_stack[-1], SkillsScreen), "Esc 未关闭 SkillsScreen"
            # Enter 选中技能：填入 "Invoke skill '<name>'" 并以用户消息发出（弹窗关闭、
            # 进入回合）；Esc（dismiss None）不发送
            prompt.focus()
            prompt.text = "/skills"
            await pilot.press("enter")
            await settle(pilot, 10)
            assert isinstance(app.screen_stack[-1], SkillsScreen)
            sk_lst = app.screen_stack[-1].query_one("#skills-list", OptionList)
            sk_idx = next(i for i in range(sk_lst.option_count)
                          if sk_lst.get_option_at_index(i).prompt.plain.startswith("smoke-skill-a"))
            sk_lst.highlighted = sk_idx
            await pilot.press("enter")
            await settle(pilot, 5)
            assert not isinstance(app.screen_stack[-1], SkillsScreen), "Enter 选中技能后弹窗应关闭"
            assert app._busy, "技能调用应以用户回合发出"
            bodies = [str(w.render()) for w in app.query(".card-body")]
            assert any("Invoke skill 'smoke-skill-a'" in b for b in bodies), bodies
            for _ in range(200):  # 等回合结束（demo agent 流式回复）
                if not app._busy:
                    break
                await pilot.pause(0.05)
            assert not app._busy, "技能回合未结束"
            print("[smoke] /skills Enter OK: fills & sends Invoke skill '<name>'")
            _skills_mod.SKILL_REGISTRY.clear()  # 无技能场景：应给出提示卡而非空弹窗
            prompt.focus()
            prompt.text = "/skills"
            await pilot.press("enter")
            await settle(pilot, 10)
            assert not app._busy and not isinstance(app.screen_stack[-1], SkillsScreen), \
                "无技能时不应打开空弹窗"
            bodies = [str(w.render()) for w in app.query(".card-body")]
            assert any("No skills found" in b for b in bodies), bodies
            _skills_mod.SKILL_REGISTRY.update(_skills_orig)  # 复原（原样放回，含磁盘扫描项）
            print("[smoke] /skills OK: picker-style popup lists all skills, empty registry notifies")
            _todo_mod.CURRENT_TODOS = []  # 复位全局（仅冒烟进程内生效）
            with BACKGROUND_LOCK:
                BACKGROUND_TASKS.pop("bg-7777", None)
    finally:
        _render._APP = None


def main() -> None:
    """--smoke 入口：跑完整 UI 自检（阻塞至结束）。"""
    asyncio.run(_smoke())
