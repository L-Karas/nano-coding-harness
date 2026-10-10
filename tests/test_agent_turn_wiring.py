"""make_agent_turn 的接线：cron / 后台任务自动线程必须拿到同一个 runtime（曾漏传参数 → 线程启动即
TypeError、cron 永不自动触发）；lead inbox 在回合结束后吸收；runtime.interrupt 作为中断回调暴露。"""
import core.loop_with_interrupt as lwi


def test_turn_wires_cron_and_absorbs_inbox(monkeypatch):
    started, submitted, saved = [], [], []

    class _Thread:
        def __init__(self, **kw):
            self.kw = kw

        def start(self):
            started.append(self.kw)

    class _Runtime:
        def run_turn(self, prepare=None):
            submitted.append("turn")
            return True

    monkeypatch.setattr(lwi.threading, "Thread", _Thread)
    monkeypatch.setattr(lwi, "AgentRuntime", _Runtime)
    monkeypatch.setattr(lwi, "consume_lead_inbox", lambda route_protocol: [{
        "from_agent": "mate", "content": "plan ready", "msg_type": "plan_approval_response",
        "metadata": {"request_id": "request_0001"},
    }])
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", saved.append)

    runtime = lwi.start_agent_runtime()
    lwi.make_agent_turn(runtime)("query")

    auto = [kw for kw in started if kw["target"] is lwi.auto_loop]
    assert auto and auto[0]["args"][0] is runtime, "自动回合线程必须拿到同一个 runtime"
    assert submitted == ["turn"], "回合必须交给 runtime 并阻塞至结束"
    assert saved and "[Inbox messages]" in saved[0]["content"], "回合后应吸收 lead inbox"
    assert "request_0001" in saved[0]["content"], "inbox 标签应带 request_id"
