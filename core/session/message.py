from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MessageUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    usage: MessageUsage = field(default_factory=MessageUsage)
