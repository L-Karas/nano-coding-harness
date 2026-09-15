from pydantic import Field

from core import cron_scheduler
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


class ListCrons(BaseTool):
    """List all registered cron jobs."""
    agent_type: set = {"main"}


class CancelCron(BaseTool):
    """Cancel a cron job by ID."""
    job_id: str = Field(description="The ID of the cron job to cancel.")

    agent_type: set = {"main"}


def run_schedule_cron(cron_expression: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
    return cron_scheduler.run_schedule_cron(cron_expression, prompt, recurring, durable)


def run_list_crons() -> str:
    return cron_scheduler.run_list_crons()


def run_cancel_cron(job_id: str) -> str:
    return cron_scheduler.run_cancel_cron(job_id)


async def run_schedule_cron_async(cron_expression: str, prompt: str, recurring: bool = True,
                                  durable: bool = True, ctx=None) -> str:
    return run_schedule_cron(cron_expression, prompt, recurring, durable)


async def run_list_crons_async(ctx=None) -> str:
    return run_list_crons()


async def run_cancel_cron_async(job_id: str, ctx=None) -> str:
    return run_cancel_cron(job_id)
