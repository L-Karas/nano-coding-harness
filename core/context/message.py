"""会话消息类型与模型请求视图。"""
import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from core.template import (
    IMAGE_OMITTED_MESSAGE,
    INJECTION_MESSAGES_PREFIX,
    INJECTION_MESSAGES_SUFFIX,
    SKILL_INVOCATION_PREFIX,
)


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
    content: str | list[dict] = ""
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


def content_text(content: Any) -> str:
    """把消息 content（str 或 OpenAI content 数组）压平为文本：数组只保留 text part。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def is_injected_content(content: Any) -> bool:
    """是否为内部注入消息（按 <injection_messages> 前后缀识别）；支持 content 数组。"""
    text = content_text(content).strip()
    return (text.startswith(INJECTION_MESSAGES_PREFIX.strip())
            and text.endswith(INJECTION_MESSAGES_SUFFIX.strip()))


def is_synthetic_content(content: Any) -> bool:
    """是否为 harness 生成的合成用户消息（标题 / fork 列表都跳过）：以 < 或 [ 开头。

    `/skills` 产生的技能调用（[Invoke skill '<name>']，可附补充信息）是用户主动发起的
    消息，不算合成：它可作会话标题，也保留在 fork 列表中。其余 < / [ 开头内容仍跳过。
    """
    text = content_text(content).lstrip()
    return text.startswith(("<", "[")) and not text.startswith(SKILL_INVOCATION_PREFIX)


def _has_image_part(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


def to_llm_messages(messages: list, *, vision: bool = True) -> list[dict]:
    """会话消息（Message 或含 id / usage / payload 的 dict）→ 模型请求视图：只保留 API 字段。

    vision=False 时，含 image_url part 的消息整条替换为 IMAGE_OMITTED_MESSAGE；
    会话里保存的消息不被修改。
    """
    view = []
    for message in messages:
        role = message_get(message, "role", "")
        content = message_get(message, "content", "")
        if not vision and _has_image_part(content):
            content = IMAGE_OMITTED_MESSAGE
        elif isinstance(content, list):
            # 请求视图不得与 Message.content 共享可变数组：before_llm 扩展的改写只留在本次请求
            content = copy.deepcopy(content)
        item = {"role": role, "content": content}
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
