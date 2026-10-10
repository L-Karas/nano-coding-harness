"""RuntimeState：单锁、类型化快照、后台任务原子出队、子代理生命周期。"""
from core.runtime_state import (BackgroundEntry, CompletedBackground, RUNTIME_STATE, RuntimeState,
                                SubagentEntry, TodoEntry)
from core.tools import ToolResult


def test_snapshot_returns_copies():
    state = RuntimeState()
    state.set_todos([TodoEntry("write tests", "in_progress")])
    snap = state.snapshot()
    assert snap.todos == [TodoEntry("write tests", "in_progress")]
    snap.todos.append(TodoEntry("later"))
    assert len(state.snapshot().todos) == 1


def test_background_lifecycle_and_atomic_pop():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    assert bg_id == "bg-0001"
    state.register_background(bg_id, "terminal(cmd)")
    assert state.snapshot().background == [BackgroundEntry(id=bg_id, status="running", tool_call="terminal(cmd)")]

    state.complete_background(bg_id, ToolResult(content="done"))
    assert state.snapshot().background == [BackgroundEntry(id=bg_id, status="completed", tool_call="terminal(cmd)")]

    done = state.pop_completed_backgrounds()
    assert done == [CompletedBackground(id=bg_id, tool_call="terminal(cmd)", result=ToolResult(content="done"))]
    assert state.snapshot().background == []
    assert state.pop_completed_backgrounds() == []


def test_running_background_is_not_popped():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(cmd)")
    assert state.pop_completed_backgrounds() == []
    assert len(state.snapshot().background) == 1


def test_background_ids_are_sequential():
    state = RuntimeState()
    assert [state.allocate_background_id() for _ in range(3)] == ["bg-0001", "bg-0002", "bg-0003"]


def test_subagent_lifecycle():
    state = RuntimeState()
    sa_id = state.allocate_subagent_id()
    assert sa_id == "sa-0001"
    state.register_subagent(sa_id, "explore")
    state.update_subagent(sa_id, "tool", "read_file")
    assert state.snapshot().subagents == [
        SubagentEntry(id=sa_id, phase="tool", detail="read_file", description="explore")]
    state.remove_subagent(sa_id)
    assert state.snapshot().subagents == []


def test_clear_resets_all_sections():
    state = RuntimeState()
    state.set_todos([TodoEntry("t")])
    state.register_background(state.allocate_background_id(), "terminal(cmd)")
    state.register_subagent(state.allocate_subagent_id(), "explore")
    state.clear()
    snap = state.snapshot()
    assert snap.todos == [] and snap.background == [] and snap.subagents == []


def test_module_singleton_exists():
    assert RUNTIME_STATE.snapshot() is not None
