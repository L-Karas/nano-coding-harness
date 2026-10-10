from typing import Literal

from pydantic import BaseModel, Field

from core.runtime_context import ToolContext
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
        return todo_write(self.todos)
