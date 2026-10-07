"""未选择模型（无可用上下文窗口）时压缩必须原样返回：阈值解析为 0 只代表“无窗口”，
不得据此触发 summarize，否则自动回合会在 prepare_messages 阶段抛错、绕过 agent 错误卡。"""
import asyncio
from types import SimpleNamespace

import core.context.compact.context_compact as compact_mod


def _fake_client():
    return SimpleNamespace(compact_threshold=lambda: 0, reserve_threshold=lambda: 0)


def test_compact_history_noops_without_context_window(monkeypatch):
    monkeypatch.setattr(compact_mod, "shared_model_client", _fake_client)
    messages = [{"role": "user", "content": "hi"}]
    result, compacted = asyncio.run(compact_mod.compact_history(messages))
    assert result is messages and compacted is False


def test_prepare_messages_noops_without_context_window(monkeypatch):
    monkeypatch.setattr(compact_mod, "shared_model_client", _fake_client)
    monkeypatch.setattr(compact_mod.CONFIGMANAGER.config, "auto_compact", True)
    messages = [{"role": "user", "content": "hi"}]
    assert asyncio.run(compact_mod.prepare_messages(messages)) is messages
