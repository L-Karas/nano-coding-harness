"""
Message BUS
Team communication is append-only JSONL mailboxes. This keeps the protocol
inspectable on disk and lets background teammates send messages.

"""
import json
import time
from dataclasses import dataclass, field, asdict

from src.config import WORKDIR, HARNESS_CONFIG_DIR
from src.log import get_logger

MAILBOX_DIR = HARNESS_CONFIG_DIR / ".mailboxes"
MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
_LOGGER = get_logger(__name__)


@dataclass
class Message:
    from_agent: str
    to_agent: str
    content: str
    msg_type: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = Message(from_agent, to_agent, content, msg_type, metadata or {})

        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(msg), ensure_ascii=False) + "\n")
            _LOGGER.info(f"[Message BUS] {from_agent} → {to_agent}: ({msg_type}) {content[:50]}")
            # terminal_print(f"  \033[33m[Message BUS] {from_agent} → {to_agent}: ({msg_type}) {content[:50]}\033[0m")

    def read(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []

        msgs = [json.loads(msg) for msg in inbox.read_text(encoding="utf-8").splitlines() if msg.strip()]
        inbox.unlink(missing_ok=True)

        _LOGGER.info(f"[Message BUS] {agent} read {len(msgs)} messages")

        return msgs


MESSAGE_BUS = MessageBus()
