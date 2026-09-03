import ast
import json
from typing import Literal

from pydantic import BaseModel, Field

import task
from tools.tool_base import BaseTool


class TodoItem(BaseModel):
    """A single todo item."""
    content: str = Field(description="The todo item description.")
    status: Literal["pending", "in_progress", "completed"] = \
        Field(default="pending", description="The status of the todo item.")


class TodoWrite(BaseTool):
    """Create and manage a task list for the current session."""
    todos: list[TodoItem] = Field(description="List of todo items to create or update.")

    agent_level: set = {"main"}


def _normalize_todos(todos):
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            # json.loads 仅 JSON 格式（只支持双引号、不支持元组/集合/None等）
            try:
                # ast.literal_eval 支持 Python 字面量（支持元组、集合、None、布尔等）
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"

    if not isinstance(todos, list):
        return None, "Error: todos must be a list"

    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"Error: todos[{i}] must contain 'content' or 'status'"
        if todo["status"] not in ["pending", "in_progress", "completed"]:
            return None, f"Error: todos[{i}] has invalid status '{todo['status']}'"

    return todos, None


def run_todo_write(todos: list) -> str:
    todos, error = _normalize_todos(todos)
    if error:
        return error
    task.CURRENT_TODOS = todos
    print(f"  \033[33m[Todo Update] updated {len(task.CURRENT_TODOS)} item(s)\033[0m")
    return f"Updated {len(task.CURRENT_TODOS)} todos"
