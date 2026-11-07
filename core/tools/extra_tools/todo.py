from typing import Literal

from pydantic import BaseModel, Field

from core.runtime_context import ToolContext
from core.runtime_state import TodoEntry
from core.todo import todo_write
from core.tools.tool_base import BaseTool


class TodoItem(BaseModel):
    """A single todo item."""
    content: str = Field(description="The todo item description.")
    status: Literal["pending", "in_progress", "completed"] = \
        Field(default="pending", description="The status of the todo item.")


class TodoWrite(BaseTool):
    """Create and update a todo list for the current session."""
    todos: list[TodoItem] = Field(description="List of todo items to create or update.")

    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        """用本次提交的条目替换当前会话的 todo 列表。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            更新后的 todo 列表文本。
        """
        return todo_write([
            TodoEntry(content=item.content, status=item.status) for item in self.todos
        ])
