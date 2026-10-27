"""add_message（append 写入）建出的会话必须能重读：文件为空时 append 会先写一个换行，
首行是空行；load_session 解析须跳过空行——否则重启后所有普通会话都读不回（返回 None）。"""


def test_reload_append_created_session(session_manager):
    session_manager.new_session()
    session_manager.add_message({"role": "user", "content": "u1"})
    session_manager.add_message({"role": "assistant", "content": "a1"})
    sid = session_manager.current_session

    session_manager.reset()  # 绕过内存短路，强制从文件重读
    reloaded = session_manager.load_session(sid)
    assert reloaded is not None
    assert [(m.role, m.content) for m in reloaded.messages] == [("user", "u1"), ("assistant", "a1")]
