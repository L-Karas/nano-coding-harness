"""会话消息 → 模型请求视图的裁剪工具。"""


def to_llm_messages(messages: list[dict]) -> list[dict]:
    """会话消息（含 id / usage / payload）→ 模型请求视图：只保留 API 字段与非空可选字段。"""
    view = []
    for message in messages:
        item = {"role": message["role"], "content": message.get("content", "")}
        if message["role"] == "assistant":
            item["reasoning_content"] = message.get("reasoning_content", "")
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        view.append(item)
    return view
