"""AgentRuntime LLM 调用失败必须同步渲染错误卡：曾只把 [Error] 落进会话不渲染，
UI 表现为「输入信息后无响应」（如 API Key 401 被静默吞掉）。"""
import core.loop_with_interrupt as lwi


def test_llm_failure_writes_session_and_renders_card(monkeypatch):
    saved, rendered = [], []

    class _Mgr:
        def load_messages(self):
            return []

        def update_messages(self, _m):
            pass

        def add_message(self, m):
            saved.append(m)

    async def _boom(**_kwargs):
        raise ValueError("bad api key")

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: ([], {}))
    monkeypatch.setattr(lwi, "SESSION_MANAGER", _Mgr())
    monkeypatch.setattr(lwi, "render_background_notification",
                        lambda msg, title: rendered.append((msg, title)))

    runtime = lwi.AgentRuntime()  # 真 runtime：走 submit / 事件循环线程，与 UI 调用路径一致
    monkeypatch.setattr(runtime, "call_llm", _boom)

    runtime.submit(runtime.run())  # submit 内部阻塞至回合结束

    error_text = "[Error] ValueError: bad api key"
    assert saved == [{"role": "assistant", "content": error_text}]
    assert rendered == [(error_text, "⚠️ Agent Error")]
