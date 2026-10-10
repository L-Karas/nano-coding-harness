"""冒烟自检 · 会话：/new 延迟建会话、会话选择弹窗（搜索过滤、150 条可达、排序、原地删除）。"""
from __future__ import annotations

from textual.widgets import Input, OptionList, Static

from core.tui.screens import SessionPickerScreen
from smoke._util import prompt_of, settle


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # /new 延迟建会话：只清空 current_session 指针，不立即 new_session（防空会话）；
    # 忙碌中 /new 被拒绝（否则本轮后续 add_message 会把回话写进新建会话）
    class _FakeMgr:
        def __init__(self):
            self.current_session = "session-old.jsonl"
            self.created = 0

        def new_session(self):
            self.created += 1
            self.current_session = f"session-new-{self.created}.jsonl"

        def reset(self):
            self.current_session = ""

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
    # 顶部搜索栏：样式 / 行为同 /skills /mcp /provider（❯ 标记、仅下边框、默认聚焦、大小写不敏感过滤）
    s_search = scr.query_one("#search-input", Input)
    assert s_search.placeholder == "Search sessions…", s_search.placeholder
    assert s_search.has_focus, "搜索栏应默认聚焦"
    s_bar = scr.query_one("#search-row")
    assert not s_bar.styles.border_top[0] and s_bar.styles.border_bottom[0] == "solid", \
        f"搜索栏应仅显示下边框: {(s_bar.styles.border_top, s_bar.styles.border_bottom)}"
    assert str(scr.query_one("#search-prompt").render()) == "❯", "搜索栏缺 ❯ 标记"
    s_search.value = "SESSION 149"  # 大写：验证忽略大小写
    await settle(pilot)
    filtered = [olist.get_option_at_index(i).id for i in range(olist.option_count)]
    assert filtered == ["session-000149.jsonl"], filtered
    s_search.value = ""
    await settle(pilot)
    assert olist.option_count == 150, "清空搜索应恢复全部会话"
    # Delete 删除会话：窗内底部红字原地确认（Esc 撤销不删 / Enter 确认后从列表移除并刷新）
    _n_before = len(scr._manager.sessions)
    await pilot.press("delete")
    await settle(pilot)
    shint = scr.query_one("#confirm-hint", Static)
    smsg = shint.render()
    assert "Delete \"" in str(smsg) and "rgb(248,113,113)" in str(smsg.spans[0].style), \
        f"会话 Delete 未就地显示红色确认: {smsg!r}"
    await pilot.press("escape")
    await settle(pilot)
    assert len(scr._manager.sessions) == _n_before and app._exception is None, \
        "取消确认不应删除会话"
    await pilot.press("delete")
    await settle(pilot)
    await pilot.press("enter")
    await settle(pilot)
    assert len(scr._manager.sessions) == _n_before - 1, "确认后应删除会话"
    assert olist.option_count == _n_before - 1, "删除后列表应刷新"
    # 过滤后 Delete：搜索栏聚焦时 delete 仍是窗级删除（priority 绑定）；目标按行 id 回查
    # （_sessions 下标已不对应过滤后的列表位置，旧实现会删错会话）
    s_search.value = "SESSION 148"
    await settle(pilot)
    filtered = [olist.get_option_at_index(i).id for i in range(olist.option_count)]
    assert filtered == ["session-000148.jsonl"], filtered
    await pilot.press("delete")
    await settle(pilot)
    f_hint = str(scr.query_one("#confirm-hint", Static).render())
    assert 'Delete "Session 148"?' in f_hint, f"过滤后 Delete 目标错误: {f_hint!r}"
    await pilot.press("enter")
    await settle(pilot)
    remaining = {s.id for s in scr._manager.sessions}
    assert "session-000148.jsonl" not in remaining, "过滤后 Delete 应删高亮会话"
    assert "session-000147.jsonl" in remaining and len(remaining) == _n_before - 2, \
        f"过滤后 Delete 删错了会话（按下标取回复现）: {len(remaining)}"
    await pilot.press("escape")
    await settle(pilot)
    assert app._exception is None, f"渲染异常: {app._exception}"
    print("[smoke] sessions picker OK: search filter, 150 sessions all reachable, no clipping")
