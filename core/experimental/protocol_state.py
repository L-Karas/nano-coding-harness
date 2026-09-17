"""
Protocol State
"""
import random
from dataclasses import dataclass, field
from time import time

from core.experimental import message_bus

PENDING_REQUESTS: dict[str, "ProtocolState"] = {}


@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default=time)


def get_request_id() -> str:
    return f"request_{random.randint(0, 1000):04d}"


def match_response(response_type: str, request_id: str, approval: bool):
    state = PENDING_REQUESTS.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    state.status = "approved" if approval else "rejected"


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    msgs = message_bus.MESSAGE_BUS.read("lead")
    if route_protocol:
        for msg in msgs:
            metadata = msg.get("metadata", {})
            request_id = metadata.get("request_id", "")
            msg_type = msg.get("msg_type", "")
            if request_id and msg_type.endswith("_response"):
                match_response(msg_type, request_id, metadata.get("approve", False))
    return msgs
