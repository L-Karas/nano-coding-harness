"""运行状态：右栏展示的 todos / 后台任务 / 子代理。

`RuntimeState` 单例持有全部状态与唯一一把锁；写侧只经具名入口，读侧只经类型化快照
（见 docs/adr/0004-runtime-state.md）。状态 module 是叶子：不 import 任何运行状态写入方。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core.tools import ToolResult


@dataclass(frozen=True)
class TodoEntry:
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


@dataclass(frozen=True)
class BackgroundEntry:
    id: str
    status: str
    tool_call: str


@dataclass(frozen=True)
class SubagentEntry:
    id: str
    phase: str
    detail: str
    description: str


@dataclass(frozen=True)
class CompletedBackground:
    id: str
    tool_call: str
    result: ToolResult


@dataclass(frozen=True)
class RuntimeSnapshot:
    todos: list[TodoEntry]
    background: list[BackgroundEntry]
    subagents: list[SubagentEntry]


class RuntimeState:
    """运行状态容器：全部读写共用一把锁；快照是副本，写入口具名。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._todos: list[TodoEntry] = []
        self._background: dict[str, BackgroundEntry] = {}
        self._background_results: dict[str, ToolResult] = {}
        self._subagents: dict[str, SubagentEntry] = {}
        self._bg_counter = 0
        self._subagent_counter = 0

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(todos=list(self._todos),
                                   background=list(self._background.values()),
                                   subagents=list(self._subagents.values()))

    def clear(self) -> None:
        """仅测试 / 冒烟复位用。"""
        with self._lock:
            self._todos = []
            self._background = {}
            self._background_results = {}
            self._subagents = {}

    # ---------- todos ----------

    def set_todos(self, todos: list[TodoEntry]) -> None:
        with self._lock:
            self._todos = list(todos)

    # ---------- background ----------

    def allocate_background_id(self) -> str:
        with self._lock:
            self._bg_counter += 1
            return f"bg-{self._bg_counter:04d}"

    def register_background(self, bg_id: str, tool_call: str) -> None:
        with self._lock:
            self._background[bg_id] = BackgroundEntry(id=bg_id, status="running", tool_call=tool_call)

    def complete_background(self, bg_id: str, result: ToolResult) -> None:
        with self._lock:
            entry = self._background.get(bg_id)
            if entry is not None:
                self._background[bg_id] = replace(entry, status="completed")
            self._background_results[bg_id] = result

    def pop_completed_backgrounds(self) -> list[CompletedBackground]:
        """同一临界区内筛选 + 出队，避免完成判定与移除之间出现串行缝隙。"""
        with self._lock:
            done_ids = [bg_id for bg_id, entry in self._background.items() if entry.status == "completed"]
            done = []
            for bg_id in done_ids:
                entry = self._background.pop(bg_id)
                done.append(CompletedBackground(id=bg_id, tool_call=entry.tool_call,
                                                result=self._background_results.pop(bg_id, None)))
            return done

    # ---------- subagents ----------

    def allocate_subagent_id(self) -> str:
        with self._lock:
            self._subagent_counter += 1
            return f"sa-{self._subagent_counter:04d}"

    def register_subagent(self, agent_id: str, description: str) -> None:
        with self._lock:
            self._subagents[agent_id] = SubagentEntry(id=agent_id, phase="thinking", detail="",
                                                      description=description)

    def update_subagent(self, agent_id: str, phase: str, detail: str = "") -> None:
        with self._lock:
            entry = self._subagents.get(agent_id)
            if entry is not None:
                self._subagents[agent_id] = replace(entry, phase=phase, detail=detail)

    def remove_subagent(self, agent_id: str) -> None:
        with self._lock:
            self._subagents.pop(agent_id, None)


RUNTIME_STATE = RuntimeState()
