"""前台回合 runner：busy 的唯一写者、工作线程契约、收尾顺序。

一个回合 = 一段占用输入条的前台工作（用户消息 / 手动压缩）。本模块隐藏：
- 忙门禁（已在跑时拒绝新的前台工作）；
- 工作线程的守护线程与异常上屏；
- 收尾顺序（先取消挂起询问，再重估任务栏进度，再复位状态行与输入条）。

UI 部件与进度计算经构造参数注入，本模块不 import Textual / ChatApp。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Optional

from core.tui.theme import PLACEHOLDER


class Turn:
    """前台回合 runner；`busy` 只由本类写。"""

    def __init__(
        self,
        *,
        prompt: Callable[[], Any],
        status: Callable[..., None],
        progress: Callable[[], None],
        cancel_asks: Callable[[], bool],
        notify: Callable[..., None],
        call_on_app_thread: Callable[[Callable[[], None]], None],
    ) -> None:
        """初始化 runner。

        Args:
            prompt: 取输入条（需要 `.text` / `.disabled` / `.placeholder` / `.focus()`）。
            status: 设置状态行（`(text, spin=...)`）。
            progress: 重估任务栏进度。
            cancel_asks: 取消挂起询问（回合收尾兜底）。
            notify: 异常 / 提示上屏（toast）。
            call_on_app_thread: 把回调送回 App 线程（App 已退出时抛 RuntimeError）。
        """
        self._prompt = prompt
        self._status = status
        self._progress = progress
        self._cancel_asks = cancel_asks
        self._notify = notify
        self._call_on_app_thread = call_on_app_thread
        self._busy = False

    @property
    def busy(self) -> bool:
        """是否有前台回合在跑。

        Returns:
            运行中为 True。
        """
        return self._busy

    def start(
        self,
        work: Callable[[], None],
        *,
        status_text: str,
        thread_name: str,
        error_title: str,
    ) -> Optional[threading.Thread]:
        """开始一个前台回合。

        Args:
            work: 工作线程主体。
            status_text: 状态行文案（如 "Working…" / "Compacting…"）。
            thread_name: 线程名。
            error_title: 异常提示标题。

        Returns:
            已启动的守护线程；已有回合在跑时为 None（调用方应提示忙）。

        成功后清空并禁用输入条、显示 spinner 状态、重估任务栏进度，然后起守护线程；
        线程结束（含异常）时经 call_on_app_thread 回到 App 线程收尾。
        """
        if self._busy:
            return None
        self._busy = True
        prompt = self._prompt()
        prompt.text = ""
        prompt.disabled = True
        self._status(status_text, spin=True)
        self._progress()
        thread = threading.Thread(target=self._run, args=(work, error_title),
                                  daemon=True, name=thread_name)
        thread.start()
        return thread

    def finish(self) -> None:
        """收尾：复位忙碌态、取消挂起询问、重估进度、清空状态行、恢复输入条。

        幂等：重复调用无副作用（回合外作答也用它就地恢复空闲界面）。
        """
        self._busy = False
        self._cancel_asks()  # 先清挂起：_progress 据此判断等待优先级
        self._progress()
        self._status("")
        prompt = self._prompt()
        prompt.placeholder = PLACEHOLDER
        prompt.disabled = False
        prompt.focus()

    def _run(self, work: Callable[[], None], error_title: str) -> None:
        """工作线程主体。

        Args:
            work: 业务逻辑。
            error_title: 异常提示标题。

        异常上屏（App 已退出则忽略）；无论成败都回 App 线程收尾。
        """
        try:
            work()
        except Exception as exc:
            self._notify(f"{type(exc).__name__}: {exc}", title=error_title, severity="error")
        finally:
            try:
                self._call_on_app_thread(self.finish)
            except RuntimeError:
                pass  # App 已退出
