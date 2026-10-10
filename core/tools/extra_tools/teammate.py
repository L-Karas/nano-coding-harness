from pydantic import Field

import core.experimental.message_bus as message_bus
import core.experimental.protocol_state as protocol_state
from core.experimental.protocol_state import consume_lead_inbox
from core.experimental.teammates import spawn_teammate_thread
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool


class SpawnTeammate(BaseTool):
    """Spawn an autonomous teammate agent."""
    name: str = Field(description="Unique name for the teammate.")
    role: str = Field(description="The role or expertise of the teammate (e.g. 'code reviewer').")
    prompt: str = Field(description="The initial task or instructions for the teammate.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        return spawn_teammate_thread(self.name, self.role, self.prompt)


class SendMessage(BaseTool):
    """Send a message to teammate agent."""
    to_agent: str = Field(description="The name of the agent to send the message to.")
    content: str = Field(description="The message content to send.")

    agent_type: set = {"main", "teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        message_bus.MESSAGE_BUS.send(tctx.agent_name if tctx else "lead", self.to_agent, self.content)
        return f"Sent message to {self.to_agent}"


class CheckInbox(BaseTool):
    """Check inbox for messages and protocol responses."""
    agent_type: set = {"main", "teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
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


class RequestShutdown(BaseTool):
    """Request a teammate to shut down."""
    teammate: str = Field(description="The name of the teammate to request shutdown from.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        req_id = protocol_state.get_request_id()
        protocol_state.PENDING_REQUESTS[req_id] = protocol_state.ProtocolState(
            request_id=req_id,
            type="shutdown",
            sender="lead",
            target=self.teammate,
            status="pending",
            payload="",
        )
        message_bus.MESSAGE_BUS.send("lead", self.teammate, "Shut down.", "shutdown_request", {"request_id": req_id})
        return f"Shutdown request sent to {self.teammate}."


class RequestPlan(BaseTool):
    """Ask a teammate to submit a plan for review."""
    teammate: str = Field(description="The name of the teammate to request a plan from.")
    task: str = Field(description="The task description for which a plan is needed.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        message_bus.MESSAGE_BUS.send("lead", self.teammate, f"Submit plan for: {self.task}.", "message")
        return f"Asked {self.teammate} to submit a plan."


class ReviewPlan(BaseTool):
    """Approve or reject a submitted plan."""
    request_id: str = Field(description="The request ID of the plan to review.")
    approve: bool = Field(description="Whether to approve (true) or reject (false) the plan.")
    feedback: str | None = Field(default=None, description="Optional feedback when rejecting a plan.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        state = protocol_state.PENDING_REQUESTS.get(self.request_id)
        if not state:
            return f"Request '{self.request_id}' not found."

        state.status = "approved" if self.approve else "rejected"
        message_bus.MESSAGE_BUS.send(
            "lead",
            state.sender,
            self.feedback or ("Approved" if self.approve else "Rejected"),
            "plan_approval_response",
            {"request_id": self.request_id, "approve": self.approve},
        )

        return f"Plan {'approved' if self.approve else 'rejected'}."


class SubmitPlan(BaseTool):
    """Submit a plan for Lead approval."""
    plan: str = Field(description="The plan content to submit for approval.")

    agent_type: set = {"teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        """Register the plan request and send it to Lead for approval."""
        from_agent = tctx.agent_name if tctx else "lead"
        req_id = protocol_state.get_request_id()
        protocol_state.PENDING_REQUESTS[req_id] = protocol_state.ProtocolState(
            request_id=req_id,
            type="plan_approval",
            sender=from_agent,
            target="lead",
            status="pending",
            payload=self.plan,
        )
        message_bus.MESSAGE_BUS.send(from_agent, "lead", self.plan, "plan_approval_request", {"request_id": req_id})
        return f"Plan submitted ({req_id})"
