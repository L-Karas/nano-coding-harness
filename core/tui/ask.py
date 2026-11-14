"""待答请求状态机（纯）：权限确认与 clarify 共用一套「至多一个挂起请求」。

只管状态与作答协议：Future 何时落地、多选如何累积、Other 文本如何归约、取消时各 kind
的默认答案。部件渲染与操作提示在 core.tui.ask_panel；测试不需要 Textual。
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum

PERMISSION_DENIED = "no"  # 取消权限确认时的默认答案（同旧版 [y/N]）
CLARIFY_CANCELLED = "[User cancelled]"  # 取消澄清时的默认答案
CLARIFY_NO_SELECTION = "[User made no selection]"


class AskKind(StrEnum):
    """询问类型。"""

    PERMISSION = "permission"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class AskRequest:
    """一次询问请求。

    Attributes:
        kind: 询问类型。
        options: clarify 候选答案；权限不用。
        multi: clarify 是否多选。
    """

    kind: AskKind
    options: tuple[str, ...] = ()
    multi: bool = False


@dataclass(frozen=True)
class AskView:
    """一次询问的当前视图：AskPanel 据此幂等重建部件。

    Attributes:
        request: 原始请求。
        chosen: 多选已勾选的下标。
        other_mode: 是否处于 Other 手动输入模式。
    """

    request: AskRequest
    chosen: frozenset[int] = frozenset()
    other_mode: bool = False


class AskState:
    """同一时刻至多一个挂起请求。

    不变式：`open` 在已有挂起请求时返回 False；`answer` / `cancel` 幂等，重复调用返回 False；
    `cancel` 一定会落地 Future（权限 → "no"，clarify → "[User cancelled]"），等待线程不会悬挂。
    """

    def __init__(self) -> None:
        """初始化空状态。"""
        self._request: AskRequest | None = None
        self._future: Future[str] | None = None
        self._chosen: set[int] = set()
        self._other = False

    def open(self, request: AskRequest, future: Future[str]) -> bool:
        """发起询问。

        Args:
            request: 询问请求。
            future: 等待作答的 Future。

        Returns:
            成功挂起为 True；已有挂起请求时为 False（调用方须自行落地 future）。
        """
        if self._future is not None:
            return False
        self._request = request
        self._future = future
        self._chosen = set()
        self._other = False
        return True

    def view(self) -> AskView | None:
        """当前挂起请求的视图。

        Returns:
            视图；无挂起请求时为 None。
        """
        request = self._request
        if request is None:
            return None
        return AskView(request=request, chosen=frozenset(self._chosen), other_mode=self._other)

    def toggle(self, index: int) -> bool:
        """切换多选项的勾选状态。

        Args:
            index: 选项下标。

        Returns:
            确实切换了为 True；无挂起请求 / 非多选 / 越界时为 False。
        """
        request = self._request
        if request is None or not request.multi or not 0 <= index < len(request.options):
            return False
        if index in self._chosen:
            self._chosen.discard(index)
        else:
            self._chosen.add(index)
        return True

    def set_other_mode(self, active: bool = True) -> bool:
        """进入 / 退出 Other 手动输入模式。

        Args:
            active: True 进入，False 退出。

        Returns:
            状态确实改变为 True；权限询问 / 无挂起请求时为 False。
        """
        request = self._request
        if request is None or request.kind is not AskKind.CLARIFY or self._other is active:
            return False
        self._other = active
        return True

    def answer(self, value: str = "") -> bool:
        """按当前模式作答并落地 Future。

        Args:
            value: 权限为 "yes" / "no"；单选为选中项文本；Other 模式为输入文本；多选忽略。

        Returns:
            确实关闭了询问为 True。

        多选（含 Other）归约为逐行 `- 选项`；多选无勾选时给占位答案；单选 Other 用原文。
        """
        request = self._request
        if request is None:
            return False
        if request.kind is AskKind.PERMISSION:
            if value not in ("yes", "no"):
                return False
            return self._resolve(value)
        if request.multi:
            return self._resolve(self._compose(request, value))
        if not value:
            return False
        return self._resolve(value)

    def cancel(self) -> bool:
        """取消当前询问（回合收尾 / Esc）。

        Returns:
            确实关闭了为 True；无挂起请求时为 False。
        """
        request = self._request
        if request is None:
            return False
        return self._resolve(PERMISSION_DENIED if request.kind is AskKind.PERMISSION
                             else CLARIFY_CANCELLED)

    def _compose(self, request: AskRequest, other: str) -> str:
        """生成多选答案。

        Args:
            request: 当前请求。
            other: Other 输入文本；为空则只用勾选项。

        Returns:
            勾选项逐行文本；Other 文本追加末行；空选择给占位文本。
        """
        chosen = [request.options[i] for i in sorted(self._chosen)]
        if other:
            chosen.append(other)
        return "\n".join(f"- {text}" for text in chosen) if chosen else CLARIFY_NO_SELECTION

    def _resolve(self, value: str) -> bool:
        """写回 Future 并清空状态。

        Args:
            value: 最终答案。

        Returns:
            恒为 True（调用方已确认有挂起请求）。
        """
        future, self._future = self._future, None
        self._request = None
        self._chosen = set()
        self._other = False
        if future is not None:
            future.set_result(value)
        return True
