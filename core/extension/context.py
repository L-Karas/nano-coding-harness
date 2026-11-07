"""外部扩展事件上下文对象：可变字段承载钩子对主流程的修改。

接口约定：
- 调用方提供的事实（`tool_name` / `cwd` / `tctx` / `finish_reason` 等）只读；
- `args` / `messages` / `result` 等提案字段可写；
- 控制状态由 `abort()` / `decide()` / `handle()` 维护，扩展不应直接伪造；
- `slots=True` 让“只读”之外也不存在可动态塞入的外来字段。
"""
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator

from core.extension.protocol import ACTIONS
from core.runtime_context import ToolContext


@dataclass(frozen=True, slots=True)
class Decision:
    """一张决定票；source / args_snapshot 由派发器提交时写入。"""

    action: str
    reason: str = ""
    source: str = ""
    args_snapshot: dict | None = None


class Ballot:
    """决定票集合：只接受合法 Decision，提交 / 过期规则由派发器驱动。"""

    __slots__ = ("_votes",)

    def __init__(self, votes: Iterable[Decision] = ()) -> None:
        """用可选初始票初始化票箱。

        Args:
            votes: 初始决定票序列。
        """
        self._votes: list[Decision] = []
        self.extend(votes)

    def vote(self, action: str, reason: str = "") -> None:
        """登记一票；action 非法立即报错。

        Args:
            action: 决定动作（allow / ask / deny）。
            reason: 投票理由。

        Raises:
            ValueError: action 不在 ACTIONS 中。
        """
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action!r}")
        self.append(Decision(action=action, reason=str(reason)))

    def append(self, decision: Decision) -> None:
        """追加一张票；元素类型或 action 非法立即报错，不把坏对象留给派发器。

        Args:
            decision: 待追加的决定票。

        Raises:
            TypeError: 传入对象不是 Decision。
            ValueError: action 不在 ACTIONS 中。
        """
        if not isinstance(decision, Decision):
            raise TypeError(
                f"decision must be Decision, got {type(decision).__name__}")
        if decision.action not in ACTIONS:
            raise ValueError(f"unknown action: {decision.action!r}")
        self._votes.append(decision)

    def extend(self, decisions: Iterable[Decision]) -> None:
        """批量追加决定票（逐个校验）。

        Args:
            decisions: 待追加的决定票序列。
        """
        for decision in decisions:
            self.append(decision)

    def commit_from(self, before: int, source: str, args_snapshot: dict | None) -> None:
        """把本次回调新增的票收敛为最后一张，并写入来源 / 参数快照。

        Args:
            before: 回调开始前的票数。
            source: 回调来源（模块名）。
            args_snapshot: 投票时的参数快照。
        """
        new = self._votes[before:]
        if not new:
            return
        last = replace(new[-1], source=source, args_snapshot=args_snapshot)
        self._votes = self._votes[:before] + [last]

    def rollback_from(self, before: int) -> None:
        """丢弃本次回调新增的票。

        Args:
            before: 回调开始前的票数。
        """
        del self._votes[before:]

    def drop_stale(self, args: object) -> int:
        """作废基于旧参数作出的票，返回丢弃数量。

        Args:
            args: 当前参数；与票内快照不同则作废。

        Returns:
            被丢弃的票数。
        """
        kept: list[Decision] = []
        dropped = 0
        for decision in self._votes:
            if decision.args_snapshot is not None and decision.args_snapshot != args:
                dropped += 1
            else:
                kept.append(decision)
        self._votes = kept
        return dropped

    def __len__(self) -> int:
        """返回票数。

        Returns:
            当前票数。
        """
        return len(self._votes)

    def __iter__(self) -> Iterator[Decision]:
        """按登记顺序迭代决定票。

        Returns:
            Decision 迭代器。
        """
        return iter(self._votes)

    def __getitem__(self, index: int | slice) -> Decision | list[Decision]:
        """按位置或切片取票。

        Args:
            index: 票的下标（支持负数）或切片。

        Returns:
            对应位置的 Decision，或切片得到的 Decision 列表。
        """
        return self._votes[index]

    def __bool__(self) -> bool:
        """票箱是否非空。

        Returns:
            有票时为 True。
        """
        return bool(self._votes)

    def __eq__(self, other: object) -> bool:
        """与 Ballot / list / tuple 比较票集合。

        Args:
            other: 待比较对象。

        Returns:
            票集合相等时为 True；不支持的类型返回 NotImplemented。
        """
        if isinstance(other, Ballot):
            return self._votes == other._votes
        if isinstance(other, (list, tuple)):
            return self._votes == list(other)
        return NotImplemented

    def __repr__(self) -> str:
        """返回票箱的调试表示。

        Returns:
            形如 ``Ballot([...])`` 的字符串。
        """
        return f"Ballot({self._votes!r})"


class _Context:
    """上下文基类：只读字段初始化后不可再赋值；slots 阻止外来字段。"""

    __slots__ = ()
    _READ_ONLY: ClassVar[frozenset[str]] = frozenset()

    def __setattr__(self, name: str, value: object) -> None:
        """写入字段；只读字段初始化后拒绝再次赋值。

        Args:
            name: 字段名。
            value: 要写入的值。

        Raises:
            AttributeError: 尝试改写已初始化的只读字段。
        """
        if name in type(self)._READ_ONLY and hasattr(self, name):
            raise AttributeError(f"{type(self).__name__}.{name} is read-only")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """删除字段；只读字段禁止删除。

        Args:
            name: 字段名。

        Raises:
            AttributeError: 尝试删除只读字段。
        """
        if name in type(self)._READ_ONLY:
            raise AttributeError(f"{type(self).__name__}.{name} is read-only")
        object.__delattr__(self, name)


@dataclass(slots=True)
class BeforeLLMContext(_Context):
    messages: list[dict]
    tools: list[dict]
    max_tokens: int
    aborted: bool = False
    abort_reason: str = ""

    _READ_ONLY = frozenset({"aborted", "abort_reason"})

    def abort(self, reason: str) -> None:
        """标记本次 LLM 调用中止并记录原因。

        Args:
            reason: 中止原因。
        """
        object.__setattr__(self, "aborted", True)
        object.__setattr__(self, "abort_reason", str(reason))


@dataclass(slots=True)
class AfterLLMContext(_Context):
    content: str | None
    tool_calls: list[dict]
    finish_reason: str = ""
    usage: Any = None
    inject_messages: list[dict] = field(default_factory=list)

    _READ_ONLY = frozenset({"finish_reason", "usage"})


@dataclass(slots=True)
class BeforeToolContext(_Context):
    tool_name: str
    args: dict
    cwd: Path | None = None  # 调用方工作目录（ToolContext.cwd）；缺省按 WORKDIR，权限门据此判定路径越界
    blocked: bool = False
    block_reason: str = ""
    decisions: Ballot = field(default_factory=Ballot)

    _READ_ONLY = frozenset({"tool_name", "cwd", "blocked", "block_reason", "decisions"})

    def __post_init__(self) -> None:
        """把传入的 list[Decision] 统一收进 Ballot。"""
        # 兼容调用方直接传 list[Decision]；统一收进 Ballot，后续只接受合法票。
        if not isinstance(self.decisions, Ballot):
            object.__setattr__(self, "decisions", Ballot(self.decisions))

    def decide(self, action: str, reason: str = "") -> None:
        """登记一票；仅 allow/ask/deny 合法，不改变 blocked。

        Args:
            action: 决定动作（allow / ask / deny）。
            reason: 投票理由。
        """
        self.decisions.vote(action, str(reason))

    def deny(self, reason: str = "") -> None:
        """登记一票拒绝；是否拦截由权限门裁决。

        Args:
            reason: 拒绝理由。
        """
        self.decide("deny", reason)

    def block(self, reason: str = "") -> None:
        """`deny()` 的旧名字，保留兼容；不会立即拦截。

        Args:
            reason: 拒绝理由。
        """
        self.deny(reason)

    def _set_blocked(self, reason: str) -> None:
        """由内置权限门 / 派发器写入最终拦截状态，不属于扩展作者接口。

        Args:
            reason: 拦截原因。
        """
        object.__setattr__(self, "blocked", True)
        object.__setattr__(self, "block_reason", str(reason))


@dataclass(slots=True)
class AfterToolContext(_Context):
    tool_name: str
    args: dict
    result: str
    is_error: bool = False
    inject_messages: list[dict] = field(default_factory=list)

    _READ_ONLY = frozenset({"tool_name", "args", "is_error"})


@dataclass(slots=True)
class ToolExecuteContext(_Context):
    tool_name: str
    args: dict
    tctx: ToolContext | None = None
    handled: bool = False
    result: str = ""
    is_error: bool = False

    _READ_ONLY = frozenset({"tool_name", "args", "tctx", "handled"})

    def handle(self, result: Any, is_error: bool = False) -> None:
        """声明接管本次执行；结果取代内置执行（只写 result 不算接管）。

        Args:
            result: 接管后的执行结果。
            is_error: 该结果是否代表错误。
        """
        object.__setattr__(self, "handled", True)
        self.result = str(result)
        self.is_error = bool(is_error)
