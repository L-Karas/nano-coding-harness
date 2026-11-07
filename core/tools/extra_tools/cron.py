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
        """注册一个 cron 任务。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            任务注册结果文本。

        Raises:
            Exception: cron 表达式非法。
        """
        return cron_scheduler.run_schedule_cron(
            self.cron_expression,
            self.prompt,
            self.recurring,
            self.durable,
        )


class ListCrons(BaseTool):
    """List all registered cron jobs."""
    agent_type: set = {"main"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        """列出当前登记的全部 cron 任务。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            任务列表文本。
        """
        return cron_scheduler.run_list_crons()


class CancelCron(BaseTool):
    """Cancel a cron job by ID."""
    job_id: str = Field(description="The ID of the cron job to cancel.")

    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        """按 id 取消一个 cron 任务。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            取消结果文本。
        """
        return cron_scheduler.run_cancel_cron(self.job_id)
