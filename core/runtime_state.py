"""运行状态：右栏展示的 todos / 后台任务 / 子代理，以及后两者的跨回合取消句柄。

`RuntimeState` 单例持有全部状态与唯一一把锁；写侧只经具名入口，读侧只经类型化快照
（见 docs/adr/0004-runtime-state.md、docs/adr/0009-background-cancel.md）。状态 module 是叶子：
不 import 任何运行状态写入方，也不 import asyncio——取消句柄是发起者提供的线程安全回调。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, TypeVar

if TYPE_CHECKING:
    from core.tools import ToolResult


DEFAULT_SUBAGENT_ROLE = "subagent"  # 直接调用方未给 role 时的兜底展示标签


@dataclass(frozen=True)
class TodoEntry:
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


@dataclass(frozen=True)
class BackgroundEntry:
    id: str
    status: str
    tool_call: str
    cancellable: bool = False
    is_subagent: bool = False  # 子代理后台任务（右栏只在 Subagents 分区展示，且不挂 bg 取消句柄）


@dataclass(frozen=True)
class SubagentEntry:
    id: str
    phase: str
    detail: str
    description: str
    goal: str = ""
    role: str = DEFAULT_SUBAGENT_ROLE
    cancellable: bool = False


_EntryT = TypeVar("_EntryT", BackgroundEntry, SubagentEntry)


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

    def has_running_background(self) -> bool:
        """是否有在跑的后台任务 / 子代理（子代理条目只在运行期存在；Todos 不算）。

        Returns:
            存在运行中的后台任务或子代理时为 True。
        """
        return bool(self.subagents) or any(bg.status == "running" for bg in self.background)


class RuntimeState:
    """运行状态容器：全部读写共用一把锁；快照是副本，写入口具名。"""

    def __init__(self) -> None:
        """初始化空状态与线程锁。"""
        self._lock = threading.Lock()
        self._todos: list[TodoEntry] = []
        self._background: dict[str, BackgroundEntry] = {}
        self._background_results: dict[str, ToolResult] = {}
        self._subagents: dict[str, SubagentEntry] = {}
        # 跨回合取消句柄：id → 线程安全回调（后台任务与子代理共用一张表，id 前缀不重）
        self._cancels: dict[str, Callable[[], None]] = {}
        self._bg_counter = 0
        self._subagent_counter = 0

    def snapshot(self) -> RuntimeSnapshot:
        """返回当前状态的只读快照。

        Returns:
            todos / background / subagents 的副本。
        """
        with self._lock:
            return RuntimeSnapshot(
                todos=list(self._todos),
                background=self._with_cancellable(self._background.values()),
                subagents=self._with_cancellable(self._subagents.values()),
            )

    def _with_cancellable(
        self,
        entries: Iterable[_EntryT],
    ) -> list[_EntryT]:
        """按句柄表实时标注可停性（句柄表是唯一事实源，条目本身不保管能力）。

        Args:
            entries: 后台任务或子代理条目。

        Returns:
            带 cancellable 标记的条目副本。
        """
        return [replace(entry, cancellable=entry.id in self._cancels) for entry in entries]

    def clear(self) -> None:
        """仅测试 / 冒烟复位用。"""
        with self._lock:
            self._todos = []
            self._background = {}
            self._background_results = {}
            self._subagents = {}
            self._cancels = {}

    # ---------- 取消 ----------

    def attach_cancel(self, work_id: str, cancel: Callable[[], None]) -> None:
        """登记一条工作的取消句柄（后台任务 / 子代理通用）。

        cancel 的契约：任何线程可调（asyncio task 需自行转投所属 loop），
        且只应影响 work_id 对应的工作，不得放大成整个回合的取消。

        Args:
            work_id: 后台任务或子代理 id。
            cancel: 线程安全的取消回调。
        """
        with self._lock:
            self._cancels[work_id] = cancel

    def cancel_work(self, work_id: str) -> bool:
        """指名取消：消费句柄并在锁外投递；无句柄或投递失败（如 loop 已关闭）返回 False。

        Args:
            work_id: 要取消的工作 id。

        Returns:
            是否成功投递取消。
        """
        with self._lock:
            cancel = self._cancels.pop(work_id, None)
        if cancel is None:
            return False
        try:
            cancel()
        except RuntimeError:
            return False
        return True

    # ---------- todos ----------

    def set_todos(self, todos: list[TodoEntry]) -> None:
        """替换当前 todo 列表。

        Args:
            todos: 新的 todo 条目。
        """
        with self._lock:
            self._todos = list(todos)

    # ---------- background ----------

    def allocate_background_id(self) -> str:
        """分配一个递增的后台任务 id。

        Returns:
            形如 ``bg-0001`` 的 id。
        """
        with self._lock:
            self._bg_counter += 1
            return f"bg-{self._bg_counter:04d}"

    def register_background(self, bg_id: str, tool_call: str, is_subagent: bool = False) -> None:
        """登记一个运行中的后台任务。

        Args:
            bg_id: 后台任务 id。
            tool_call: 发起该任务的工具调用描述。
            is_subagent: 是否为子代理任务。
        """
        with self._lock:
            self._background[bg_id] = BackgroundEntry(id=bg_id, status="running", tool_call=tool_call,
                                                      is_subagent=is_subagent)

    def complete_background(self, bg_id: str, result: ToolResult) -> None:
        """标记后台任务完成并保存结果。

        Args:
            bg_id: 后台任务 id。
            result: 任务结果。
        """
        with self._lock:
            entry = self._background.get(bg_id)
            if entry is not None:
                self._background[bg_id] = replace(entry, status="completed")
            self._background_results[bg_id] = result
            self._cancels.pop(bg_id, None)

    def pop_completed_backgrounds(self) -> list[CompletedBackground]:
        """同一临界区内筛选 + 出队，避免完成判定与移除之间出现串行缝隙。

        Returns:
            已完成并出队的后台任务列表。
        """
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
        """分配一个递增的子代理 id。

        Returns:
            形如 ``sa-0001`` 的 id。
        """
        with self._lock:
            self._subagent_counter += 1
            return f"sa-{self._subagent_counter:04d}"

    def register_subagent(self, agent_id: str, description: str, goal: str = "",
                          *, role: str = DEFAULT_SUBAGENT_ROLE) -> None:
        """登记一个处于 thinking 阶段的子代理。

        Args:
            agent_id: 子代理 id。
            description: 任务描述。
            goal: 单行目标说明。
            role: 展示用角色标签。
        """
        clean_role = " ".join((role or "").split()) or DEFAULT_SUBAGENT_ROLE
        with self._lock:
            self._subagents[agent_id] = SubagentEntry(
                id=agent_id,
                phase="thinking",
                detail="",
                description=description,
                goal=goal,
                role=clean_role,
            )

    def update_subagent(
        self,
        agent_id: str,
        phase: str,
        detail: str = "",
    ) -> None:
        """更新子代理阶段与详情。

        Args:
            agent_id: 子代理 id。
            phase: 当前阶段。
            detail: 阶段详情。
        """
        with self._lock:
            entry = self._subagents.get(agent_id)
            if entry is not None:
                self._subagents[agent_id] = replace(entry, phase=phase, detail=detail)

    def remove_subagent(self, agent_id: str) -> None:
        """移除子代理条目及其取消句柄。

        Args:
            agent_id: 子代理 id。
        """
        with self._lock:
            self._subagents.pop(agent_id, None)
            self._cancels.pop(agent_id, None)


RUNTIME_STATE = RuntimeState()
