from typing import Literal

from pydantic import BaseModel, Field
from core.tools.tool_base import BaseTool
from core.todo import todo_write

class TodoItem(BaseModel):
    """A single todo item."""
    content: str = Field(description="The todo item description.")
    status: Literal["pending", "in_progress", "completed"] = \
        Field(default="pending", description="The status of the todo item.")


class TodoWrite(BaseTool):
    """Create and update a todo list for the current session."""
    todos: list[TodoItem] = Field(description="List of todo items to create or update.")

    agent_type: set = {"main"}


def run_todo_write(todos: list) -> str:
    return todo_write(todos)
