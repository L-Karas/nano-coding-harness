"""AgentRuntime LLM 调用失败必须同步渲染错误卡：曾只把 [Error] 落进会话不渲染，
UI 表现为「输入信息后无响应」（如 API Key 401 被静默吞掉）。
错误信息与任务执行无关，不再落会话历史，只渲染通知卡。"""
import core.agent_loop as lwi
from core import interaction
from types import SimpleNamespace


class _StubClient:
    """避免真实 shared_model_client 在测试中刷新全局单例（会污染后续测试的 provider）。"""
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            return object()
        return create


def test_llm_failure_renders_card_without_session_write(monkeypatch):
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
    monkeypatch.setattr(interaction, "render_background_notification",
                        lambda msg, title: rendered.append((msg, title)))

    runtime = lwi.AgentRuntime()  # 真 runtime：走 submit / 事件循环线程，与 UI 调用路径一致
    monkeypatch.setattr(lwi, "run_agent_step", _boom)

    runtime.run_turn()  # submit 内部阻塞至回合结束

    error_text = "[Error] ValueError: bad api key"
    assert saved == [], "错误信息不进消息历史"
    assert rendered == [(error_text, "⚠️ Agent Error")]
