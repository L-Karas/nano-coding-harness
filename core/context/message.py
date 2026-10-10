"""会话消息类型与模型请求视图。"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


def _get_message_id() -> str:
    return f"message-{uuid.uuid4().hex}"


def _get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Message:
    """
    Message 类，记录消息内容
    payload 属性用于记录附带信息，例如修改文件后的 git diff 信息，用于信息重新渲染时使用
    """
    role: Literal["user", "assistant", "tool"]
    id: str = field(default_factory=_get_message_id)
    content: str = ""
    reasoning_content: str = ""
    tool_call_id: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    payload: Any = ""
    timestamp: str = field(default_factory=_get_timestamp)


def as_message(message: "Message | dict") -> Message:
    """把 StepOutcome / 扩展注入的 dict 归一为 Message；已是 Message 原样返回。"""
    if isinstance(message, Message):
        return message
    return Message(**message)


def message_get(message: "Message | dict", key: str, default: Any = None) -> Any:
    """Message / dict 统一读取：compact 与 to_llm_messages 共用。"""
    if isinstance(message, Message):
        return getattr(message, key, default)
    return message.get(key, default)


def message_set(message: "Message | dict", key: str, value: Any) -> None:
    """Message / dict 统一原地写入。"""
    if isinstance(message, Message):
        setattr(message, key, value)
    else:
        message[key] = value


def to_llm_messages(messages: list) -> list[dict]:
    """会话消息（Message 或含 id / usage / payload 的 dict）→ 模型请求视图：只保留 API 字段。"""
    view = []
    for message in messages:
        role = message_get(message, "role", "")
        item = {"role": role, "content": message_get(message, "content", "")}
        if role == "assistant":
            item["reasoning_content"] = message_get(message, "reasoning_content", "")
        tool_calls = message_get(message, "tool_calls")
        if tool_calls:
            item["tool_calls"] = tool_calls
        tool_call_id = message_get(message, "tool_call_id")
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        view.append(item)
    return view
