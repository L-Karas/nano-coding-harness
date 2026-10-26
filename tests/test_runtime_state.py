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


def test_register_background_records_subagent_origin():
    """标记来源：子代理后台任务 vs 其他后台任务（右栏据此分区展示）。"""
    state = RuntimeState()
    state.register_background("bg-0001", "terminal(pytest)")
    state.register_background("bg-0002", "spawn_subagent(description=explore)", is_subagent=True)
    other, subagent = state.snapshot().background
    assert other.is_subagent is False
    assert subagent.is_subagent is True


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
    state.register_subagent(sa_id, "explore", "find the entry point", role="reviewer")
    state.update_subagent(sa_id, "tool", "read_file")
    assert state.snapshot().subagents == [
        SubagentEntry(
            id=sa_id,
            phase="tool",
            detail="read_file",
            description="explore",
            goal="find the entry point",
            role="reviewer",
        )]
    state.remove_subagent(sa_id)
    assert state.snapshot().subagents == []


def test_subagent_role_defaults_and_normalizes():
    """role 缺省/空白回退为 subagent；直接调用方传入的换行与多余空白归一为单行标签。"""
    state = RuntimeState()
    state.register_subagent("sa-1", "explore")
    state.register_subagent("sa-2", "explore", role="  code\nreviewer  ")
    state.register_subagent("sa-3", "explore", role="   ")
    assert [sa.role for sa in state.snapshot().subagents] == ["subagent", "code reviewer", "subagent"]


def test_snapshot_has_running_background_only_for_live_work():
    """任务栏判活谓词：仅 running 的后台任务 / 存在的子代理为真；Todos 与已完成项不算。"""
    state = RuntimeState()
    assert not state.snapshot().has_running_background()

    state.set_todos([TodoEntry("write tests", "in_progress")])
    assert not state.snapshot().has_running_background()

    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    assert state.snapshot().has_running_background()

    state.complete_background(bg_id, ToolResult(content="done"))
    assert not state.snapshot().has_running_background()  # 完成待弹出：不再算运行中

    sa_id = state.allocate_subagent_id()
    state.register_subagent(sa_id, "explore")
    assert state.snapshot().has_running_background()

    state.remove_subagent(sa_id)
    assert not state.snapshot().has_running_background()


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


def test_cancel_work_invokes_attached_handle_once():
    """取消句柄一次消费：点两下第二次是 no-op，不会二次投递。"""
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    calls = []
    state.attach_cancel(bg_id, lambda: calls.append(bg_id))

    assert state.cancel_work(bg_id) is True
    assert calls == [bg_id]
    assert state.cancel_work(bg_id) is False
    assert calls == [bg_id]


def test_background_entry_is_cancellable_only_with_handle():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    assert state.snapshot().background[0].cancellable is False
    state.attach_cancel(bg_id, lambda: None)
    assert state.snapshot().background[0].cancellable is True


def test_subagent_entry_is_cancellable_only_with_handle():
    state = RuntimeState()
    sa_id = state.allocate_subagent_id()
    state.register_subagent(sa_id, "explore")
    assert state.snapshot().subagents[0].cancellable is False
    state.attach_cancel(sa_id, lambda: None)
    assert state.snapshot().subagents[0].cancellable is True


def test_completing_background_drops_cancel_handle():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.attach_cancel(bg_id, lambda: None)
    state.complete_background(bg_id, ToolResult(content="done"))
    assert state.snapshot().background[0].cancellable is False
    assert state.cancel_work(bg_id) is False


def test_removing_subagent_drops_cancel_handle():
    state = RuntimeState()
    sa_id = state.allocate_subagent_id()
    state.register_subagent(sa_id, "explore")
    state.attach_cancel(sa_id, lambda: None)
    state.remove_subagent(sa_id)
    assert state.cancel_work(sa_id) is False


def test_clear_drops_cancel_handles():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.attach_cancel(bg_id, lambda: None)
    state.clear()
    assert state.cancel_work(bg_id) is False


def test_cancel_work_reports_undeliverable_handle():
    """投递失败（如 loop 已关闭）不抛出，返回 False。"""
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")

    def boom():
        raise RuntimeError("event loop is closed")

    state.attach_cancel(bg_id, boom)
    assert state.cancel_work(bg_id) is False
