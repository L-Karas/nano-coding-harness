"""todo_write：ToolPool 校验参数后，带 status 写入 RuntimeState 并回显清单。"""
import asyncio

from core.runtime_state import RuntimeState, TodoEntry
from core.todo import todo
from core.tools import ToolPool
from core.tools.extra_tools.todo import TodoWrite


def _pool() -> ToolPool:
    return ToolPool({"todo_write": TodoWrite})


def _state(monkeypatch) -> RuntimeState:
    state = RuntimeState()
    monkeypatch.setattr(todo, "RUNTIME_STATE", state)
    return state


def test_pool_execute_writes_todos_with_status(monkeypatch):
    state = _state(monkeypatch)
    result = _pool().execute_sync("todo_write", {"todos": [
        {"content": "write tests", "status": "in_progress"},
        {"content": "ship it", "status": "completed"},
    ]})
    assert not result.is_error
    assert state.snapshot().todos == [
        TodoEntry("write tests", "in_progress"),
        TodoEntry("ship it", "completed"),
    ]


def test_pool_async_execute_writes_todos(monkeypatch):
    state = _state(monkeypatch)
    result = asyncio.run(_pool().execute("todo_write", {"todos": [{"content": "a", "status": "pending"}]}))
    assert not result.is_error
    assert state.snapshot().todos == [TodoEntry("a")]


def test_pool_accepts_empty_todos(monkeypatch):
    state = _state(monkeypatch)
    result = _pool().execute_sync("todo_write", {"todos": []})
    assert not result.is_error
    assert state.snapshot().todos == []


def test_pool_rejects_invalid_status(monkeypatch):
    state = _state(monkeypatch)
    result = _pool().execute_sync("todo_write", {"todos": [{"content": "a", "status": "done"}]})
    assert result.is_error
    assert state.snapshot().todos == []
