"""手动 /compact（auto_compact=False）跳过阈值判断：低于阈值也直接做全量摘要；
自动压缩（auto_compact=True）仍按阈值触发；未选择模型时两者都无法压缩。"""
import asyncio
from types import SimpleNamespace

import core.context.compact.context_compact as compact_mod
from tests.support import FakeStream, StubModelClient, text_chunk


def _stub_client(trigger_tokens: int = 10**9) -> StubModelClient:
    return StubModelClient(trigger_tokens=trigger_tokens, response=FakeStream([text_chunk("summary")]))


def test_manual_compact_skips_threshold(monkeypatch):
    fake_client = _stub_client()
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hi"}]

    result, compacted = asyncio.run(compact_mod.compact_history(messages, auto_compact=False))

    assert compacted is True
    assert len(result) == 1
    assert result[0].content == "<compacted_messages>\nsummary\n</compacted_messages>"
    assert fake_client.calls, "手动压缩应调用模型做摘要"


def test_auto_compact_still_noops_below_threshold(monkeypatch):
    fake_client = _stub_client()
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hi"}]

    result, compacted = asyncio.run(compact_mod.compact_history(messages))

    assert result is messages and compacted is False
    assert fake_client.calls == []


def test_manual_compact_still_noops_without_context_window(monkeypatch):
    fake_client = SimpleNamespace(compact_trigger_tokens=lambda: 0, compact_reserve_tokens=lambda: 0)
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hi"}]

    result, compacted = asyncio.run(compact_mod.compact_history(messages, auto_compact=False))

    assert result is messages and compacted is False
