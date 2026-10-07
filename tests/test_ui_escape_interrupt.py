"""Esc 的中断策略：权限确认优先（挂起请求 → 拒绝），确认结束后回合仍在进行才中断 agent，
并 notify「User interrupted」。"""
from core.tui.ui_textual import ChatApp


def test_escape_denies_permission_first_then_interrupts():
    calls = []
    app = ChatApp(handle_query=lambda query: None, on_interrupt=lambda: calls.append("interrupt"))
    app._answer_permission = lambda value: calls.append(f"deny:{value}")
    app._set_status_text = lambda *a, **k: None
    app.notify = lambda *a, **k: calls.append((a, k))

    app._perm_pending, app._busy = True, True
    app.action_deny_permission()
    assert calls == ["deny:no"], "权限确认必须优先于中断（且不打断）"

    app._perm_pending = False  # 确认已处理，回合仍在进行
    app.action_deny_permission()
    assert calls == ["deny:no", "interrupt", (("User interrupted",), {})], "确认处理完后 Esc 才中断并提示"

    app._busy = False  # 空闲：Esc 空操作
    app.action_deny_permission()
    assert calls == ["deny:no", "interrupt", (("User interrupted",), {})]
