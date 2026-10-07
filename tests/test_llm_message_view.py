"""to_llm_messages：会话全量消息裁成 API 视图——剔除 id/usage/payload，空可选字段不入请求。"""
from core.context import to_llm_messages


def test_strips_metadata_and_empty_fields():
    tool_calls = [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
    full = [
        {"role": "user", "id": "m1", "content": "hi", "reasoning_content": "", "tool_calls": [],
         "tool_call_id": "", "usage": {}, "payload": [["+", 1, "x"]]},
        {"role": "assistant", "id": "m2", "content": "", "reasoning_content": "think",
         "tool_calls": tool_calls, "tool_call_id": "", "usage": {"total_tokens": 3}, "payload": ""},
        {"role": "tool", "id": "m3", "content": "ok", "reasoning_content": "", "tool_calls": [],
         "tool_call_id": "c1", "usage": {}, "payload": ""},
    ]

    assert to_llm_messages(full) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "reasoning_content": "think", "tool_calls": tool_calls},
        {"role": "tool", "content": "ok", "tool_call_id": "c1"},
    ]
