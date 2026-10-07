"""冒烟自检 · @ 文件补全：同级列表 / 目录下钻 / 全树相似匹配 / 补全不发送。"""
from __future__ import annotations

from textual.widgets import OptionList

from smoke._util import prompt_of, settle, type_query
from core.tui.widgets import _match_project_entries


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

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
    # 大小写不敏感（同名文件如 tests/test_tui_context_length.py 也会命中，不假设唯一）
    await type_query(prompt, pilot, "view @TUI")
    assert "core/tui/" in prompt._file_candidates, prompt._file_candidates
    assert all(c.rstrip("/").rsplit("/", 1)[-1].lower().find("tui") >= 0
               for c in prompt._file_candidates), prompt._file_candidates
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
