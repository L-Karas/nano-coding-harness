"""手动压缩路径（/compact 工具与 TUI 指令）不带回合上下文：summarize_history(ctx=None)
不得触到 ctx——此前 `ctx.raise_if_cancelled()` 在任何非 ctx 调用上直接 AttributeError，
使 compact_history 只能由 agent 回合内部调用。"""
import asyncio
from types import SimpleNamespace

import core.context.compact.context_compact as compact_mod


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def close(self):
        pass


def _chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def test_summarize_history_without_ctx(monkeypatch):
    async def _create(**kwargs):  # 与 AsyncOpenAI 一致：create(...) 需 await 才拿到流
        return _FakeStream([_chunk("Earlier context "), _chunk("summarized.")])

    fake_client = SimpleNamespace(get_model_client=lambda async_client=False: _create,
                                  clamp_max_tokens=lambda requested: requested)
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: fake_client)
    messages = [{"role": "user", "content": "hi"}]
    assert asyncio.run(compact_mod.summarize_history(messages)) == "Earlier context summarized."


def test_summarize_history_clamps_max_tokens(monkeypatch):
    """总结调用同样受当前模型最大输出钳制。"""
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return _FakeStream([_chunk("summary")])

    fake_client = SimpleNamespace(get_model_client=lambda async_client=False: _create,
                                  clamp_max_tokens=lambda requested: 7_777)
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: fake_client)
    asyncio.run(compact_mod.summarize_history([{"role": "user", "content": "hi"}]))
    assert captured["max_tokens"] == 7_777


def test_compact_history_uses_sub_model_client(monkeypatch):
    """子代理压缩（sub_model=True）必须用子代理 client 总结（与对话同模型，缓存命中），不得回退主 client；
    且触发阈值/保留预算取自该 client 的当前模型，而不是配置里的参考值。"""
    async def _create(**kwargs):
        return _FakeStream([_chunk("summary")])

    fake_client = SimpleNamespace(get_model_client=lambda async_client=False: _create,
                                  compact_threshold=lambda: 1,
                                  reserve_threshold=lambda: 0,
                                  clamp_max_tokens=lambda requested: requested)

    def fail():
        raise AssertionError("应使用子代理 client，而非主 client")

    monkeypatch.setattr(compact_mod, "shared_model_client", fail)
    monkeypatch.setattr(compact_mod, "shared_sub_model_client", lambda: fake_client)
    monkeypatch.setattr(compact_mod, "CONFIGMANAGER",
                        SimpleNamespace(config=SimpleNamespace(summary_max_tokens=10)))
    result, compacted = asyncio.run(compact_mod.compact_history([{"role": "user", "content": "hi"}],
                                                                auto_compact=False, sub_model=True))
    assert compacted is True
    assert len(result) == 1
    assert result[0].role == "user"
    assert result[0].content == "<compacted_messages>\nsummary\n</compacted_messages>"


def test_find_index_to_split_uses_passed_reserve_budget():
    """切分预算由调用方显式传入（不再读全局配置）。"""
    messages = [{"role": "user", "content": "a" * 40},
                {"role": "user", "content": "b" * 40},
                {"role": "user", "content": "c" * 40}]
    assert compact_mod.find_index_to_split(messages, reserve_threshold=0) == len(messages)
    assert compact_mod.find_index_to_split(messages, reserve_threshold=10**9) == 0
