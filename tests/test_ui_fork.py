"""新增 /fork 指令：指令表紧跟 /compact；预览/标题截断共用 core.context.session.MESSAGE_PREVIEW_CHARS；
选中用户消息 → fork_session 分叉新会话 + 重放历史 + 原文填回输入栏；
无 SessionManager / 回合进行中 / 无用户消息时只提示，不弹窗。

弹窗与 App 不跑事件循环：push_screen / 渲染 / 输入栏打桩，直接驱动被测分支。"""
import io
from types import SimpleNamespace

from rich.console import Console

import core.tui.commands as commands
from core.context.session import MESSAGE_PREVIEW_CHARS
from core.tui.screens.fork import ForkScreen, _preview
from core.tui.ui_textual import ChatApp
from core.tui.utils import SLASH_COMMANDS


class _Message:
    def __init__(self, mid, content, timestamp="2026-01-02 03:04:05"):
        self.id = mid
        self.content = content
        self.timestamp = timestamp


class _FakeManager:
    """与 SessionManager 同契约：load_user_messages / fork_session / load_session"""

    def __init__(self, user_messages=None, forked_session=None):
        self._user_messages = user_messages or []
        self._forked_session = forked_session
        self.forked = []
        self.current_session = "cur"

    def load_user_messages(self):
        return self._user_messages or None

    def fork_session(self, message_id):
        self.forked.append(message_id)
        return self._forked_session

    def load_session(self, session_id=""):
        return self._forked_session


class _FakePrompt:
    """_CommandInput（TextArea）替身：document.end 随 text 变化，记录光标置位与聚焦"""

    def __init__(self, events):
        self.text = "old"
        self.cursor_location = None
        self._events = events

    @property
    def document(self):
        return SimpleNamespace(end=(0, len(self.text)))

    def focus(self):
        self._events.append(("focus",))


def _app(monkeypatch, events):
    """不跑事件循环的 ChatApp：渲染 / 推屏 / 输入栏打桩，返回 (app, prompt, pushed)"""
    monkeypatch.setattr(commands, "render_background_notification",
                        lambda body, title="": events.append(("notice", title, body)))
    monkeypatch.setattr(commands, "render_session_history", lambda session: events.append(("render", session)))
    app = ChatApp(handle_query=lambda query: None, on_interrupt=lambda: None)
    app._clear_cards = lambda: events.append(("clear",))
    prompt = _FakePrompt(events)
    app._prompt = lambda: prompt
    pushed = []
    app.push_screen = lambda screen, callback=None: pushed.append((screen, callback))
    return app, prompt, pushed


def test_fork_registered_right_after_compact():
    assert SLASH_COMMANDS[SLASH_COMMANDS.index("/compact") + 1] == "/fork"


def test_preview_truncates_to_named_constant():
    assert _preview("x" * (MESSAGE_PREVIEW_CHARS + 1)) == "x" * MESSAGE_PREVIEW_CHARS + "…"
    assert _preview("short") == "short"
    assert _preview("a\nb   c") == "a b c"  # 压平空白为单行


class _FakeOptionList:
    """_reload 未挂载路径的 OptionList 替身"""

    def __init__(self):
        self.options = []
        self.highlighted = None

    def set_options(self, options):
        self.options = list(options)


def _render(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=80, no_color=True).print(renderable)
    return buf.getvalue()


def test_fork_options_show_timestamp_to_seconds():
    screen = ForkScreen([_Message("m1", "hello world", "2026-01-02 03:04:05")])
    fake = _FakeOptionList()
    screen._list = lambda: fake  # 未挂载：不查询部件
    screen._reload()

    text = _render(fake.options[0].prompt)
    assert "hello world" in text
    assert "2026-01-02 03:04:05" in text, "选项应展示到秒的时间戳"


def test_open_fork_guards(monkeypatch):
    events = []
    app, _prompt, pushed = _app(monkeypatch, events)
    app._open_fork()  # 未接 SessionManager
    assert "unavailable" in events[-1][2] and not pushed

    events.clear()
    app._busy = True
    app._manager = _FakeManager([_Message("m1", "x")])
    app._open_fork()  # 回合进行中：换会话须拒绝
    assert events == [("notice", "⏳ Busy", "Previous turn is still running, please wait…")] and not pushed

    events.clear()
    app._busy = False
    app._manager = _FakeManager()  # load_user_messages -> None
    app._open_fork()
    assert "No user messages" in events[-1][2] and not pushed


def test_fork_pick_replays_and_fills_prompt(monkeypatch):
    events = []
    app, prompt, pushed = _app(monkeypatch, events)
    session = SimpleNamespace(messages=["m1", "a1"])
    messages = [_Message("m1", "first"), _Message("m2", "second")]
    app._manager = _FakeManager(messages, forked_session=session)

    app._open_fork()
    assert len(pushed) == 1 and isinstance(pushed[0][0], ForkScreen)
    assert [m.content for m in pushed[0][0]._messages] == ["first", "second"]

    pushed[0][1]("m2")  # 模拟选中第二条用户消息
    assert app._manager.forked == ["m2"]
    assert [e[0] for e in events] == ["clear", "render", "focus"], events
    assert events[1][1] is session, "重放的不是分叉后的新会话"
    assert prompt.text == "second", "选中消息原文未填回输入栏"
    assert prompt.cursor_location == (0, len("second")), "光标应置于填入内容末尾"


def test_fork_pick_empty_prefix_clears_board(monkeypatch):
    """选中首条消息：分叉历史为空，只清板 + 填回输入栏，不回放（也不建会话文件）。"""
    events = []
    app, prompt, pushed = _app(monkeypatch, events)
    session = SimpleNamespace(messages=[])
    app._manager = _FakeManager([_Message("m1", "first")], forked_session=session)

    app._open_fork()
    pushed[0][1]("m1")
    assert [e[0] for e in events] == ["clear", "focus"], events
    assert prompt.text == "first"
    assert prompt.cursor_location == (0, len("first"))


def test_fork_pick_failure_notifies(monkeypatch):
    events = []
    app, prompt, _pushed = _app(monkeypatch, events)
    app._manager = _FakeManager()  # fork_session -> None
    app._on_fork_picked("m1", [])
    assert events[-1][1] == "⚠️ Fork" and "not found" in events[-1][2]
    assert prompt.text == "old" and prompt.cursor_location is None and events[0][0] != "clear"
