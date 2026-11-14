"""前台回合 runner（Turn）：busy 门禁、工作线程、收尾顺序。纯测试，不启动 Textual。"""
import threading

from core.tui.turn import Turn


class _Prompt:
    def __init__(self) -> None:
        self.text = "draft"
        self.disabled = False
        self.placeholder = ""
        self.focused = False

    def focus(self) -> None:
        self.focused = True


class _Harness:
    def __init__(self) -> None:
        self.prompt = _Prompt()
        self.statuses = []
        self.progress = 0
        self.cancelled = 0
        self.notices = []
        self.finished = []
        self.turn = Turn(
            prompt=lambda: self.prompt,
            status=lambda text, spin=False: self.statuses.append((text, spin)),
            progress=self._progress,
            cancel_asks=self._cancel,
            notify=lambda *a, **k: self.notices.append((a, k)),
            call_on_app_thread=lambda fn: (self.finished.append(fn), fn())[1],
        )

    def _progress(self) -> None:
        self.progress += 1

    def _cancel(self) -> bool:
        self.cancelled += 1
        return True


def test_start_clears_prompt_and_runs_work():
    h = _Harness()
    release = threading.Event()
    ran = []

    def work() -> None:
        ran.append(True)
        release.wait(2)

    thread = h.turn.start(work, status_text="Working…", thread_name="t", error_title="⚠️")
    assert thread is not None and h.turn.busy
    assert h.prompt.text == "" and h.prompt.disabled
    assert h.statuses[0] == ("Working…", True)
    assert h.progress == 1

    assert h.turn.start(lambda: None, status_text="x", thread_name="t2", error_title="⚠️") is None, \
        "已有回合在跑时拒绝"

    release.set()
    thread.join(2)
    assert ran == [True]
    assert not h.turn.busy


def test_finish_restores_idle_ui_in_order_and_is_idempotent():
    h = _Harness()
    thread = h.turn.start(lambda: None, status_text="Working…", thread_name="t", error_title="⚠️")
    thread.join(2)

    assert h.cancelled == 1, "先取消挂起询问"
    assert h.progress == 2, "开始 + 收尾各重估一次"
    assert h.statuses[-1] == ("", False), "收尾清空状态行"
    assert h.prompt.placeholder and not h.prompt.disabled and h.prompt.focused

    h.turn.finish()  # 幂等：回合外作答也用它就地恢复
    assert h.cancelled == 2 and h.progress == 3


def test_worker_exception_is_notified_and_still_finishes():
    h = _Harness()

    def boom() -> None:
        raise RuntimeError("boom")

    thread = h.turn.start(boom, status_text="Working…", thread_name="t", error_title="⚠️ Agent Error")
    thread.join(2)

    assert h.notices[0][0] == ("RuntimeError: boom",)
    assert h.notices[0][1] == {"title": "⚠️ Agent Error", "severity": "error"}
    assert not h.turn.busy and h.statuses[-1] == ("", False)


def test_dispatch_failure_after_app_exit_is_ignored():
    h = _Harness()
    h.turn._dispatch = lambda fn: (_ for _ in ()).throw(RuntimeError("app exited"))
    thread = h.turn.start(lambda: None, status_text="Working…", thread_name="t", error_title="⚠️")
    thread.join(2)  # 不得抛到线程外
