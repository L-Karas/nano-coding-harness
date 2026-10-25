"""AgentRuntime LLM 调用失败必须同步 toast 报错：曾只把 [Error] 落进会话不渲染，
UI 表现为「输入信息后无响应」（如 API Key 401 被静默吞掉）。
错误信息与任务执行无关，不再落会话历史，只渲染一条 error 级通知。"""
import core.agent_loop as lwi
from core import interaction
from core.agent_step import StepOutcome
from types import SimpleNamespace


class _StubClient:
    """避免真实 shared_model_client 在测试中刷新全局单例（会污染后续测试的 provider）。"""
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self):
        async def create(**kwargs):
            return object()
        return create


def test_llm_failure_notifies_without_session_write(monkeypatch):
    saved, rendered = [], []

    class _Mgr:
        def load_messages(self):
            return []

        def update_messages(self, _m):
            pass

        def add_message(self, m):
            saved.append(m)

    async def _boom(*_args, **_kwargs):
        raise ValueError("bad api key")

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    monkeypatch.setattr(lwi, "shared_model_client", lambda: _StubClient())
    monkeypatch.setattr(lwi, "SESSION_MANAGER", _Mgr())
    monkeypatch.setattr(interaction, "notify",
                        lambda msg, title, severity: rendered.append((msg, title, severity)))

    runtime = lwi.AgentRuntime()  # 真 runtime：走 submit / 事件循环线程，与 UI 调用路径一致
    monkeypatch.setattr(lwi, "run_agent_step", _boom)

    runtime.run_turn()  # submit 内部阻塞至回合结束

    error_text = "[Error] ValueError: bad api key"
    assert saved == [], "错误信息不进消息历史"
    assert rendered == [(error_text, "⚠️ Agent Error", "error")]


def test_retry_limit_notifies_error(monkeypatch):
    """截断恢复超过 max_recovery_retries：同样走 error 级 toast，不静默退出。"""
    saved, rendered = [], []

    class _Mgr:
        def load_messages(self):
            return []

        def update_messages(self, _m):
            pass

        def add_message(self, m):
            saved.append(m)

    async def _length(*_args, **_kwargs):
        return StepOutcome(
            assistant_message={"role": "assistant", "content": "half"},
            followup_messages=[],
            tool_calls=[],
            finish_reason="length",
            usage={},
        )

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    monkeypatch.setattr(lwi, "shared_model_client", lambda: _StubClient())
    monkeypatch.setattr(lwi, "SESSION_MANAGER", _Mgr())
    monkeypatch.setattr(lwi, "run_agent_step", _length)
    monkeypatch.setattr(lwi, "CONFIGMANAGER", SimpleNamespace(config=SimpleNamespace(
        default_max_tokens=100,
        escalated_max_tokens=200,
        max_recovery_retries=1,
        enable_experimental_tools=False,
    )))
    monkeypatch.setattr(interaction, "notify",
                        lambda msg, title, severity: rendered.append((msg, title, severity)))

    lwi.AgentRuntime().run_turn()

    assert rendered == [("Agent exceeded maximum retry limits", "⚠️ Agent Error", "error")]
    assert [m["content"] for m in saved].count(lwi.CONTINUATION_PROMPT) == 1, \
        "max_recovery_retries=1 应只续写一次"
