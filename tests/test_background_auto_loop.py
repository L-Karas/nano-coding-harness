"""空闲时后台任务完成 → 自动开一轮读取结果（同 cron，无需用户输入触发）；未完的不触发。
后台任务结果走 notice 卡片，cron 注入等其它提示走 notify（toast）。"""
from types import SimpleNamespace

import core.agent_loop as lwi
import core.background_task as bt
from core import interaction
from core.runtime_state import RuntimeState
from core.tools import ToolResult


class _Runtime:
    def __init__(self, calls):
        self.calls = calls

    def run_turn(self, prepare=None):
        if prepare is not None and not prepare():
            return False
        self.calls.append("turn")
        return True


def _stop_after(monkeypatch, sleeps: int):
    """死循环跑够 sleeps 次后抛 SystemExit 退出（测试同步驱动，不开真线程）。"""
    state = {"n": 0}

    def sleep(_seconds):
        state["n"] += 1
        if state["n"] > sleeps:
            raise SystemExit

    monkeypatch.setattr(lwi.time, "sleep", sleep)
    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])


def test_idle_completed_task_triggers_one_auto_turn(monkeypatch):
    calls, added, notices, cards = [], [], [], []
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal()")
    state.complete_background(bg_id, ToolResult(content="real output"))

    _stop_after(monkeypatch, 3)
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", added.append)
    monkeypatch.setattr(interaction, "notify", lambda *a, **k: notices.append(a))
    monkeypatch.setattr(interaction, "render_background_notification", lambda *a, **k: cards.append(a))

    try:
        lwi.auto_loop(_Runtime(calls))
    except SystemExit:
        pass

    assert calls == ["turn"], "空闲完成任务应自动提交一轮，且结果消费后不再重复提交"
    assert added and "real output" in added[0]["content"], "结果应作为注入消息写入会话"
    assert cards and "real output" in cards[0][0], "后台任务结果应渲染 notice 卡片"
    assert notices == [], "后台任务结果不走 toast"
    assert state.pop_completed_backgrounds() == [], "结果出队后不应残留"


def test_cron_fires_notify_not_background_card(monkeypatch):
    """cron 注入不是后台任务结果：只发 toast，不渲染 notice 卡片。"""
    calls, added, notices, cards = [], [], [], []
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)
    queue = [SimpleNamespace(prompt="daily report")]

    _stop_after(monkeypatch, 3)
    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [queue.pop()] if queue else [])
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", added.append)
    monkeypatch.setattr(interaction, "notify", lambda *a, **k: notices.append((a, k)))
    monkeypatch.setattr(interaction, "render_background_notification", lambda *a, **k: cards.append(a))

    try:
        lwi.auto_loop(_Runtime(calls))
    except SystemExit:
        pass

    assert calls == ["turn"]
    assert notices and "daily report" in notices[0][0][0]
    assert notices[0][1].get("title") == "⏰ Cron Triggered"
    assert cards == [], "cron 注入不应渲染后台结果卡片"


def test_running_task_does_not_trigger(monkeypatch):
    calls, added = [], []
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)
    state.register_background(state.allocate_background_id(), "terminal()")

    _stop_after(monkeypatch, 2)
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", added.append)

    try:
        lwi.auto_loop(_Runtime(calls))
    except SystemExit:
        pass

    assert calls == [] and added == [], "未完成的任务不应触发回合"
