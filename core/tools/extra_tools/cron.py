from pydantic import Field

from core import cron_scheduler
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool


class ScheduleCron(BaseTool):
    """Schedule a cron job. cron is 5-field: min hour dom month dow. For one-shot reminders, compute the target minute and set recurring=false."""
    cron_expression: str = Field(description="5-field cron expression "
                                             "(minute hour day-of-month month day-of-week).")
    prompt: str = Field(description="The prompt to enqueue at each fire time.")
    recurring: bool = Field(default=True,
                            description="Whether the job repeats on every cron match (default: true).")
    durable: bool = Field(default=True,
                          description="Whether the job persists across sessions (default: true).")

    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        return cron_scheduler.run_schedule_cron(self.cron_expression, self.prompt, self.recurring, self.durable)


class ListCrons(BaseTool):
    """List all registered cron jobs."""
    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        return cron_scheduler.run_list_crons()


class CancelCron(BaseTool):
    """Cancel a cron job by ID."""
    job_id: str = Field(description="The ID of the cron job to cancel.")

    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        return cron_scheduler.run_cancel_cron(self.job_id)
