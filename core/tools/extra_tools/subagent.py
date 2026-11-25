from pathlib import Path
from typing import Annotated, Optional

from pydantic import Field, StringConstraints, model_validator

from core.client import ModelChoice, validate_model_choice
from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent in the background to handle a complex subtask; keep working and its conclusion arrives later as a background-task notification. Optionally pick this subagent's model with model_choice (call list_models first); omit it to use the configured default sub model."""
    description: str = Field(description="The task description for the subagent to complete.")
    goal: str = Field(description="A short one-line description of the subagent's goal, shown in the TUI "
                                   "when the entry is expanded.")
    role: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32,
                                           pattern=r"^[^\r\n]+$")] = Field(
        description="A short role label for the subagent (e.g. reviewer, researcher), shown in the TUI in place "
                    "of the subagent id.")
    working_directory: str = Field(default="",
                                   description="Directory the subagent works in. Relative paths resolve against the "
                                               "main agent's working directory; leave empty to keep the same working "
                                               "directory as the main agent.")
    model_choice: Optional[ModelChoice] = Field(
        default=None,
        description="Optional model override for this subagent. When set, both model ('provider:model') and "
                    "thinking_level are required. Omit the whole object to use the configured default sub model.")

    agent_type: set = {"main"}
    parallel_safe: bool = True

    @model_validator(mode="after")
    def _validate_model_choice(self) -> "SpawnSubagent":
        """在工具构造期校验显式模型：未知模型 / 未配置 provider 直接成为同步工具错误。

        Returns:
            校验通过的自身。

        Raises:
            ValueError: 模型未注册或 provider 没有 API key。
        """
        if self.model_choice is not None:
            validate_model_choice(self.model_choice)
        return self

    async def arun(self, tctx: ToolContext | None = None) -> str:
        """在后台启动子代理并返回启动确认文本。

        Args:
            tctx: 运行时上下文；提供工作目录与所属 agent run。

        Returns:
            子代理启动结果文本。

        Raises:
            ValueError: 指定的工作目录不存在或不是目录。
        """
        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        work_dir = base
        if self.working_directory:
            work_dir = (base / Path(self.working_directory).expanduser()).resolve()
            if not work_dir.is_dir():
                raise ValueError(f"working_directory '{work_dir}' does not exist or is not a directory")
        return await spawn_subagent(self.description, tctx.agent_run if tctx else None,
                                    goal=self.goal, role=self.role, working_directory=work_dir,
                                    model_choice=self.model_choice)
