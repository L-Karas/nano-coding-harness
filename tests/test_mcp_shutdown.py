"""退出收尾：/quit（App.exit → main 返回 → 解释器收尾）时 MCP loop 上不能留销毁告警。

7b55886 起每个 server 有一个常驻 `_manage_server` task 停在 `commands.get()`。它随进程退出
被回收时，asyncio 会打印：

    Task was destroyed but it is pending!
    Exception ignored while closing generator ... RuntimeError: no running event loop

前者是 Task 销毁告警（常驻 task 是进程级的，`_manage_server` 里已显式关掉）；后者来自
`finally` 里的 `asyncio.current_task()`（协程在 loop 外被关闭时取不到 running loop）。

告警只在「退出时那个 task 会被 GC」的形状下出现：server 连上了反而不打印（传输对象把它挂住），
连接失败时 5/5 必现。所以子进程用例用连不上的 server 造这个形状。
"""
import asyncio
import gc
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import core.mcp.mcp_client as mcp_client

_REPO_ROOT = Path(__file__).resolve().parents[1]

_EXIT_SCRIPT = """
import core.mcp.mcp_client as mcp_client

# 连不上的 server：_manage_server 走 except 分支后停在 commands.get()
mcp_client._read_config = lambda: {"mcpServers": {"broken": {"command": "no-such-command-xyz"}}}
mcp_client.get_client_manager()
print("exiting", flush=True)
"""


class _FakeGroup:
    """够 ClientSessionGroup 的形：建连成功、断连无副作用，tools 为空。"""

    def __init__(self, component_name_hook=None) -> None:
        self.tools: dict = {}

    async def __aenter__(self) -> "_FakeGroup":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def connect_to_server(self, params) -> SimpleNamespace:
        return SimpleNamespace(server_info=SimpleNamespace(name=params.command))

    async def disconnect_from_server(self, session) -> None:
        pass


def test_exit_leaves_no_pending_mcp_tasks():
    """子进程像 /quit 一样直接退出：stderr 里不能出现销毁告警。"""
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", _EXIT_SCRIPT],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "exiting" in result.stdout
    assert "Task was destroyed" not in result.stderr, result.stderr
    assert "Exception ignored" not in result.stderr, result.stderr


def test_manage_server_finalized_outside_loop_is_quiet():
    """解释器收尾时协程在 loop 外被关闭：finally 不能再取 running loop。"""
    manager = mcp_client.ClientManager({"mcpServers": {}})
    manager.session_group = _FakeGroup()
    loop = asyncio.new_event_loop()
    commands: asyncio.Queue = asyncio.Queue()
    ready = asyncio.Event()
    task = loop.create_task(
        manager._manage_server("fake", {"command": "fake"}, commands, ready))
    try:
        loop.run_until_complete(asyncio.sleep(0.05))  # 跑到 commands.get() 挂起
        assert not task.done()

        unraisable = []
        original = sys.unraisablehook
        sys.unraisablehook = unraisable.append
        try:
            # queue 的 getter future 会把 task / 协程经回调串成环：先全部放开才收得到协程
            del task, commands
            gc.collect()
        finally:
            sys.unraisablehook = original

        assert ready.is_set(), "finally 至少跑到 ready.set()"
        assert not unraisable, [f"{e.exc_type.__name__}: {e.exc_value}" for e in unraisable]
    finally:
        loop.close()
