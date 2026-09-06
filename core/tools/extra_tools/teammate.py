import core.message_bus as message_bus
import core.protocol_state as protocol_state
from core.protocol_state import consume_lead_inbox
from pydantic import Field
from core.teammates import spawn_teammate_thread
from core.tools.tool_base import BaseTool


class SpawnTeammate(BaseTool):
    """Spawn an autonomous teammate agent."""
    name: str = Field(description="Unique name for the teammate.")
    role: str = Field(description="The role or expertise of the teammate (e.g. 'code reviewer').")
    prompt: str = Field(description="The initial task or instructions for the teammate.")

    agent_level: set = {"main"}


class SendMessage(BaseTool):
    """Send a message to teammate agent."""
    to_agent: str = Field(description="The name of the agent to send the message to.")
    content: str = Field(description="The message content to send.")

    agent_level: set = {"main", "teammate"}


class CheckInbox(BaseTool):
    """Check inbox for messages and protocol responses."""
    agent_level: set = {"main", "teammate"}


class RequestShutdown(BaseTool):
    """Request a teammate to shut down."""
    teammate: str = Field(description="The name of the teammate to request shutdown from.")

    agent_level: set = {"main"}


class RequestPlan(BaseTool):
    """Ask a teammate to submit a plan for review."""
    teammate: str = Field(description="The name of the teammate to request a plan from.")
    task: str = Field(description="The task description for which a plan is needed.")

    agent_level: set = {"main"}


class ReviewPlan(BaseTool):
    """Approve or reject a submitted plan."""
    request_id: str = Field(description="The request ID of the plan to review.")
    approve: bool = Field(description="Whether to approve (true) or reject (false) the plan.")
    feedback: str | None = Field(default=None, description="Optional feedback when rejecting a plan.")

    agent_level: set = {"main"}


class SubmitPlan(BaseTool):
    """Submit a plan for Lead approval."""
    plan: str = Field(description="The plan content to submit for approval.")

    agent_level: set = {"teammate"}


def run_submit_plan(from_agent: str, plan: str) -> str:
    """Register the plan request and send it to Lead for approval."""
    req_id = protocol_state.get_request_id()
    protocol_state.PENDING_REQUESTS[req_id] = protocol_state.ProtocolState(
        request_id=req_id,
        type="plan_approval",
        sender=from_agent,
        target="lead",
        status="pending",
        payload=plan,
    )
    message_bus.MESSAGE_BUS.send(from_agent, "lead", plan, "plan_approval_request", {"request_id": req_id})
    return f"Plan submitted ({req_id})"


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to_agent: str, content: str) -> str:
    message_bus.MESSAGE_BUS.send("lead", to_agent, content)
    return f"Sent message to {to_agent}"


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(Inbox empty)"

    lines = []
    for msg in msgs:
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        tag = f" [{msg['msg_type']} request_id: {request_id}]" if request_id else f" [{msg['msg_type']}]"
        lines.append(f"  [{msg['from_agent']}]{tag} {msg['content'][:500]}")

    return "\n".join(lines)


def run_request_shutdown(teammate: str) -> str:
    req_id = protocol_state.get_request_id()
    protocol_state.PENDING_REQUESTS[req_id] = protocol_state.ProtocolState(
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
    state = protocol_state.PENDING_REQUESTS.get(request_id)
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
