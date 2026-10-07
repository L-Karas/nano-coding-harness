"""空闲时后台任务完成 → 自动开一轮读取结果（同 cron，无需用户输入触发）；未完的不触发。"""
import core.background_task as bt
import core.loop_with_interrupt as lwi


class _Runtime:
    def __init__(self, calls):
        self.calls = calls

    def run(self):
        return "coro"

    def submit(self, coro):
        self.calls.append(coro)


def _stop_after(monkeypatch, sleeps: int):
    """死循环跑够 sleeps 次后抛 SystemExit 退出（测试同步驱动，不开真线程）。"""
    state = {"n": 0}

    def sleep(_seconds):
        state["n"] += 1
        if state["n"] > sleeps:
            raise SystemExit

    monkeypatch.setattr(lwi.time, "sleep", sleep)
    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])


def _cleanup(bg_id: str):
    with bt.BACKGROUND_LOCK:
        bt.BACKGROUND_TASKS.pop(bg_id, None)
        bt.BACKGROUND_RESULTS.pop(bg_id, None)


def test_idle_completed_task_triggers_one_auto_turn(monkeypatch):
    calls, added, rendered = [], [], []
    _stop_after(monkeypatch, 3)
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", added.append)
    monkeypatch.setattr(lwi, "render_background_notification", lambda *a, **k: rendered.append(a))

    with bt.BACKGROUND_LOCK:
        bt.BACKGROUND_TASKS["bg-0001"] = {"tool_call_id": "t-1", "tool_call": "terminal()", "status": "completed"}
        bt.BACKGROUND_RESULTS["bg-0001"] = "real output"
    try:
        try:
            lwi.auto_loop(_Runtime(calls))
        except SystemExit:
            pass
    finally:
        _cleanup("bg-0001")

    assert calls == ["coro"], "空闲完成任务应自动提交一轮，且结果消费后不再重复提交"
    assert added and "real output" in added[0]["content"], "结果应作为注入消息写入会话"
    assert rendered, "自动回合应有通知上屏"


def test_running_task_does_not_trigger(monkeypatch):
    calls, added = [], []
    _stop_after(monkeypatch, 2)
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", added.append)

    with bt.BACKGROUND_LOCK:
        bt.BACKGROUND_TASKS["bg-0002"] = {"tool_call_id": "t-2", "tool_call": "terminal()", "status": "running"}
    try:
        try:
            lwi.auto_loop(_Runtime(calls))
        except SystemExit:
            pass
    finally:
        _cleanup("bg-0002")

    assert calls == [] and added == [], "未完成的任务不应触发回合"
