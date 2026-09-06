import ast
import json
from dataclasses import dataclass
from typing import Literal

from core.log import get_logger

_LOGER = get_logger(__name__)
CURRENT_TODOS: list["Todo"] = []


@dataclass
class Todo:
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


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
                return None, "todos must be a list or JSON array string"

    if not isinstance(todos, list):
        return None, "todos must be a list"

    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"todos[{i}] must contain 'content' or 'status'"
        if todo["status"] not in ["pending", "in_progress", "completed"]:
            return None, f"todos[{i}] has invalid status '{todo['status']}'"

    todos = [Todo(**todo) for todo in todos]

    return todos, None


def todo_write(todos: list) -> str:
    global CURRENT_TODOS

    todos, error = _normalize_todos(todos)
    if error:
        raise Exception(f"run todo write error: {error}")
    CURRENT_TODOS = todos
    _LOGER.info(f"[Todo Update] updated {len(CURRENT_TODOS)} item(s)")
    return f"Updated todos. Current todos:\n {CURRENT_TODOS}"