"""
Protocol State
"""
import random
from dataclasses import dataclass, field
from time import time

import message_bus

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


# ----- Lead Protocol Tools ----------

def run_request_shutdown(teammate: str) -> str:
    req_id = get_request_id()
    PENDING_REQUESTS[req_id] = ProtocolState(
        request_id=req_id,
        type="shutdown",
        sender="lead",
        target=teammate,
        status="pending",
        payload="",
    )
    message_bus.MESSAGE_BUS.send("lead", teammate, "Shut down.", "shutdown_request", {"request_id": req_id})
    return f"Shutdown request sent to {teammate}."


def run_request_plan(teammate: str, task: str) -> str:
    message_bus.MESSAGE_BUS.send("lead", teammate, f"Submit plan for: {task}.", "message")
    return f"Asked {teammate} to submit a plan."


def run_review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    state = PENDING_REQUESTS.get(request_id)
    if not state:
        return f"Request '{request_id}' not found."

    state.status = "approved" if approve else "rejected"
    message_bus.MESSAGE_BUS.send(
        "lead",
        state.sender,
        feedback or ("Approved" if approve else "Rejected"),
        "plan_approval_response",
        {"request_id": request_id, "approve": approve},
    )

    return f"Plan {'approved' if approve else 'rejected'}."