"""render_session_history 回放顺序：一条助手消息的多个 tool_call 应与各自 tool 结果交错渲染。"""
import core.tui.render as render
from core.context.session.session import Message, Session


def _capture(monkeypatch):
    events = []
    monkeypatch.setattr(render, "render_user_input", lambda text: events.append(("user", text)))
    monkeypatch.setattr(render, "render_tool_call", lambda name, args: events.append(("call", name)))
    monkeypatch.setattr(render, "render_tool_result", lambda out: events.append(("result", out)))
    monkeypatch.setattr(render, "render_tool_result_diff", lambda rows: events.append(("diff", rows)))
    monkeypatch.setattr(render, "render_assistant_response", lambda text: events.append(("assistant", text)))
    return events


def test_calls_interleave_with_results(monkeypatch):
    events = _capture(monkeypatch)
    session = Session(messages=[
        Message(role="user", content="hi"),
        Message(role="assistant", tool_calls=[
            {"id": "a", "function": {"name": "read", "arguments": "{}"}},
            {"id": "b", "function": {"name": "terminal", "arguments": "{}"}},
        ]),
        Message(role="tool", tool_call_id="b", content="B"),  # 结果乱序也应各归其位
        Message(role="tool", tool_call_id="a", content="A", payload=[("-", 1, "x")]),
        Message(role="assistant", content="done"),
    ])
    render.render_session_history(session)
    assert events == [
        ("user", "hi"),
        ("call", "read"), ("diff", [("-", 1, "x")]), ("result", "A"),
        ("call", "terminal"), ("result", "B"),
        ("assistant", "done"),
    ]


def test_content_renders_before_tool_calls(monkeypatch):
    """助手消息同时有 content 和 tool_calls 时，content 卡片先于工具卡片渲染（与流式实况一致）。"""
    events = _capture(monkeypatch)
    session = Session(messages=[
        Message(role="assistant", content="let me check", tool_calls=[
            {"id": "a", "function": {"name": "read", "arguments": "{}"}},
        ]),
        Message(role="tool", tool_call_id="a", content="A"),
        Message(role="assistant", content="done"),
    ])
    render.render_session_history(session)
    assert events == [
        ("assistant", "let me check"),
        ("call", "read"), ("result", "A"),
        ("assistant", "done"),
    ]


def test_unpaired_call_and_orphan_result(monkeypatch):
    """无结果的 tool_call 只渲染调用；无对应调用的 tool 消息在原位置渲染一次。"""
    events = _capture(monkeypatch)
    session = Session(messages=[
        Message(role="assistant", tool_calls=[{"id": "a", "function": {"name": "read", "arguments": "{}"}}]),
        Message(role="tool", tool_call_id="z", content="orphan"),
    ])
    render.render_session_history(session)
    assert events == [("call", "read"), ("result", "orphan")]
