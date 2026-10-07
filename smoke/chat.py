"""冒烟自检 · 启动与聊天区：欢迎标题 / 卡片折叠 / 流式 / 滚动 / 指令补全。"""
from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import OptionList, Static

from core.tui.render import (
    render_background_notification,
    render_tool_call,
    render_tool_result,
    render_tool_result_diff,
    render_user_input,
)
from core.tui.cards import _CappedCardBody
from smoke._util import settle
from core.tui.widgets import _CommandInput


async def run(app, pilot) -> None:

    await pilot.pause(0.2)
    prompt = app.query_one("#prompt", _CommandInput)
    left_w = app.query_one("#left").region.width  # 左栏宽（4:1 分栏后为终端 80%）
    # 空聊板欢迎标题：figlet 大字标题（small，绿色）+ 小字副标题，无动画
    welcome = app.query_one("#welcome", Static)
    welcome_text = str(welcome.render())
    assert welcome.styles.display != "none", "空聊板应显示欢迎标题"
    assert len(welcome_text.splitlines()) >= 4 and "Enter your query" in welcome_text, \
        "欢迎标题应含大字标题与副标题文案"
    t0 = str(welcome.render().spans)
    await pilot.pause(0.3)
    assert str(welcome.render().spans) == t0, "欢迎标题不应有动画"
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
    assert welcome.styles.display == "none", "首卡上屏后欢迎标题应隐藏"
    render_tool_result("line\n" * 15)  # 触发截断提示（含方括号），回归渲染期 MissingStyle
    render_tool_call("terminal", list(range(15)))  # 17 行参数 JSON → 同样折叠为 10 行 + 1 行提示
    render_tool_result("x" * 2000)  # 单行超长：按换行后的视觉行计数，同样只占 10 行 + 1 行提示
    await settle(pilot, 10, 0.05)
    assert app._exception is None, f"渲染异常: {app._exception}"
    capped = [w for w in app.query(".card-body")
              if isinstance(w, _CappedCardBody) and w.has_class("-expandable")]
    assert len(capped) == 3, f"应 3 张折叠卡: {len(capped)}"
    hints = [w.render_line(w.size.height - 1).text for w in capped]
    assert "... [truncated 6 lines] · click to expand" in hints[0], hints[0]
    assert "... [truncated 7 lines] · click to expand" in hints[1], hints[1]
    assert capped[2].size.height == 11 and "truncated" in hints[2], \
        f"单行 2000 字符应按视觉行折叠为 10 行 + 1 行提示: {capped[2].size.height} 行"
    # 截断卡片点击展开：真实鼠标点击折叠的 diff 卡末行提示 → 全文（含第 11~15 行）；
    # 再点展开态末行（折叠提示）收回截断
    render_tool_result_diff([("+", i, f"add {i}") for i in range(1, 16)])
    await settle(pilot, 10, 0.05)
    assert app.query("#chat .card.diff"), "diff 预览未渲染为 diff 卡"
    exp_bodies = [w for w in app.query(".card-body") if w.has_class("-expandable")]
    assert len(exp_bodies) == 4, f"应 4 张可展开卡片: {len(exp_bodies)}"
    diff_w = exp_bodies[-1]
    await pilot.click(diff_w, offset=(2, diff_w.region.height - 1))  # 末行=提示行
    await settle(pilot, 5)
    assert "+15 │ add 15" in str(diff_w.render()), "点击未展开 diff 全文"
    assert "· click to collapse" in diff_w.render_line(diff_w.size.height - 1).text, \
        "展开态末行应为收回提示"
    await pilot.click(diff_w, offset=(2, diff_w.region.height - 1))
    await settle(pilot, 5)
    assert "... [truncated 5 lines]" in diff_w.render_line(diff_w.size.height - 1).text, \
        "再点未收回截断"
    # 结果/调用卡：同一展开逻辑（exp_bodies 按挂载序 = result / tool / 超长单行 / diff）
    for w, tail in ((exp_bodies[0], None), (exp_bodies[1], "  14")):
        app._toggle_expand(w)
        await settle(pilot, 5)
        full = str(w.render())
        if tail is None:
            assert full.count("line\n") == 15, f"展开应显示全部 15 行: {full!r}"
        else:
            assert tail in full, f"展开应显示被折叠的参数尾部: {full!r}"
        assert "· click to collapse" in w.render_line(w.size.height - 1).text, \
            "展开态末行应为收回提示"
        app._toggle_expand(w)
        await settle(pilot, 5)
        assert "... [truncated " in w.render_line(w.size.height - 1).text, "收回后应回到截断态"
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
    # 后台通知卡：标题固定常驻，正文超 10 行折叠 + 点击展开/收回（与工具结果卡同款机制）
    render_background_notification("\n".join(f"notice {i}" for i in range(15)),
                                   title="🔔 Background Task")
    await settle(pilot, 10, 0.05)
    notice = app.query("#chat .card.notice .card-body").last()
    assert isinstance(notice, _CappedCardBody) and notice.has_class("-expandable"), \
        "长后台通知卡应为可折叠的 _CappedCardBody"
    assert "Background Task" in notice.render_line(0).text, "标题行应固定常驻"
    assert notice.size.height == 12, \
        f"折叠态应为 1 行标题 + 10 行正文 + 1 行提示: {notice.size.height}"
    assert "... [truncated 5 lines] · click to expand" in \
        notice.render_line(notice.size.height - 1).text, "折叠态末行应为展开提示"
    await pilot.click(notice, offset=(2, notice.region.height - 1))
    await settle(pilot, 5)
    assert notice.size.height == 17, f"展开态应显示完整 15 行正文: {notice.size.height}"
    assert "· click to collapse" in notice.render_line(notice.size.height - 1).text, \
        "展开态末行应为收回提示"
    await pilot.click(notice, offset=(2, notice.region.height - 1))
    await settle(pilot, 5)
    assert "... [truncated 5 lines] · click to expand" in \
        notice.render_line(notice.size.height - 1).text, "再点未收回截断"
    print("[smoke] background notice card OK: click expands/collapses")
    cards = list(app.query("#chat .card"))
    assert len(cards) >= 5, f"卡片数量不足: {len(cards)}"
    # 回归：app.css 不得用「通用选择器 + 顺序伪类」规则（如 *:last-child）—— Textual 的
    # _selector_names 每个部件都含 "*"，中招后全 app 部件都带 order 样式标记，每次
    # mount 都要给已有兄弟重跑样式表，会话一长就是 O(N²) 卡顿
    assert not any(c._has_order_style for c in cards), \
        "聊天卡片带 order 样式标记：检查 app.css 是否有 *:last-child 类规则"
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
    # 末块 margin-bottom 归零（:last-child 规则仍生效，且已限定 MarkdownBlock 类型）
    assert md.query("MarkdownBlock")[-1].styles.margin.bottom == 0, "末块 margin-bottom 未归零"
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
    # 指令补全：/ 前缀输入时输入框下方弹出候选 OptionList（仅此时可见），↑/↓+Tab/Enter 接受；其它输入不显示
    prompt.focus()
    suggest = app.query_one("#cmd-suggest", OptionList)
    prompt.text = "/"
    await settle(pilot)
    assert suggest.styles.display != "none", "输入 / 未弹出候选 OptionList"
    assert prompt._candidates == ["/new", "/sessions", "/compact", "/fork", "/skills", "/mcp", "/provider",
                                  "/model", "/effort", "/settings", "/login", "/exit"], prompt._candidates
    # 候选行 = 指令名列（含别名括注）+ 空距 + 简短说明（选项 prompt 为分段 Text，
    # plain 即整行字符），各行说明左端对齐于同一列
    assert [suggest.get_option_at_index(i).prompt.plain
            for i in range(suggest.option_count)] == [
               "/new               Start a fresh session",
               "/sessions          Open the session picker",
               "/compact           Compact the conversation history",
               "/fork              Fork session from a user message",
               "/skills            List available skills",
               "/mcp               List configured MCP servers",
               "/provider          Configure API providers",
               "/model             Switch the active model",
               "/effort            Set the thinking effort",
               "/settings          Edit agent settings",
               "/login (logout)    Register/unregister custom provider or model",
               "/exit (quit)       Quit the app"], \
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
    assert prompt._candidates == ["/sessions", "/skills", "/settings"], prompt._candidates
    assert suggest.option_count == 3, f"过滤候选条数错误: {suggest.option_count}"
    await pilot.press("enter")  # Enter 应用高亮项（首项 /sessions）并提交
    await settle(pilot, 20)
    assert not app._busy, "Enter 应提交补全后的 /sessions（走会话分支，不起 agent 回合）"
    bodies = [str(w.render()) for w in app.query(".card-body")]
    assert any("SessionManager not connected" in b for b in bodies), "Enter 未提交 /sessions"
    prompt.text = "/q"  # /quit 别名前缀 → 命中 /exit 行
    await settle(pilot)
    assert prompt._candidates == ["/exit"], prompt._candidates
    assert suggest.option_count == 1 and suggest.get_option_at_index(0).prompt.plain == \
           "/exit (quit)       Quit the app", "别名行未显示括注"
    prompt.text = "/S"  # 大写前缀同样命中（忽略大小写）
    await settle(pilot)
    assert prompt._candidates == ["/sessions", "/skills", "/settings"], prompt._candidates
    prompt.text = "/xyz"
    await settle(pilot)
    assert suggest.styles.display == "none", "无匹配不应显示列表"
    prompt.text = "hello world"
    await settle(pilot)
    assert suggest.styles.display == "none", "普通消息不应显示列表"
    print(f"[smoke] OK, {len(cards)} cards rendered, scrollable, optionlist completion OK")
