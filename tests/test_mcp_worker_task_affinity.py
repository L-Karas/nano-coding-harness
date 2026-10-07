"""回归：MCP 会话的 enter/exit（connect/disconnect）必须发生在同一个 asyncio Task。

anyio 的 cancel scope / task group 要求同 task enter/exit。历史上有两处跨 task：
1) disconnect 被 ``run_coroutine_threadsafe`` 丢进新 task；
2) connect/disconnect 经 ``asyncio.gather`` —— gather 会把每个 coroutine 包成新 task。

现在每个 server 由一个常驻 task 拥有（_manage_server）：connect/disconnect 都发生在
该 task 内。本测试走真实 ClientManager / 后台配置同步路径，只把 ClientSessionGroup 换成
带真实 anyio task group 的假实现；任何一处回退都会让 disconnect 报
RuntimeError: Attempted to exit cancel scope in a different task / that isn't the current
scope。注意该错误在老代码里是「记日志后继续」，所以必须断言 disconnect 真的完成。
"""
import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import anyio
import pytest

import core.mcp.mcp_client as mcp_client


@contextlib.asynccontextmanager
async def _anyio_group() -> AsyncGenerator[None, None]:
    """模拟 mcp stdio_client / ClientSession：建连时进入 anyio task group，退出前先 cancel。"""
    async with anyio.create_task_group() as tg:
        tg.start_soon(anyio.sleep, 3600)
        try:
            yield
        finally:
            tg.cancel_scope.cancel()


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.server_info = SimpleNamespace(name=name)


class _FakeGroup:
    """假 ClientSessionGroup：connect/disconnect 进出真实 anyio task group，并按 session 记下 task。"""

    def __init__(self, component_name_hook=None) -> None:
        self.tools: dict = {}
        self.stacks: dict[_FakeSession, contextlib.AsyncExitStack] = {}
        self.connect_tasks: dict[_FakeSession, asyncio.Task] = {}
        self.disconnect_tasks: dict[_FakeSession, asyncio.Task] = {}
        self.disconnected: list[_FakeSession] = []

    async def __aenter__(self) -> "_FakeGroup":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def connect_to_server(self, params) -> _FakeSession:
        session = _FakeSession(params.command)
        self.connect_tasks[session] = asyncio.current_task()
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(_anyio_group())
        self.stacks[session] = stack
        return session

    async def disconnect_from_server(self, session) -> None:
        self.disconnect_tasks[session] = asyncio.current_task()
        await self.stacks[session].aclose()  # 跨 task 时这里抛 anyio 的 RuntimeError
        self.disconnected.append(session)


def _reset_worker() -> None:
    """停掉本用例启动的 manager（各 server 常驻 task）并清模块单例，保证干净起点。"""
    manager = mcp_client.peek_client_manager()
    if manager is not None:
        try:
            asyncio.run_coroutine_threadsafe(manager.aclose(), mcp_client._get_loop()).result(5)
        except Exception:
            pass
    if mcp_client._manager_future is not None:
        mcp_client._manager_future.cancel()
    mcp_client._reset_manager_future()
    mcp_client._last_attempt_at = 0.0


def _wait_until(pred, timeout: float = 5.0) -> bool:
    """后台同步不阻塞调用方：等常驻 task 把连接状态收敛到预期。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_connect_and_disconnect_run_in_the_same_task(monkeypatch, tmp_path):
    config_file = tmp_path / ".mcp.json"
    configs = {"mcpServers": {"a": {"command": "a", "args": []},
                              "b": {"command": "b", "args": []}}}
    config_file.write_text(json.dumps(configs), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "MCP_CONFIG_FILE", config_file)
    group = _FakeGroup()
    monkeypatch.setattr(mcp_client, "ClientSessionGroup", lambda component_name_hook=None: group)
    _reset_worker()
    try:
        manager = mcp_client.get_client_manager()  # 首连 a、b（各自常驻 task，并发）
        # 配置变化：删除 a；修改 b（disconnect + reconnect）；新增 c。
        # get_client_manager 只触发后台配置同步（立即返回），等常驻 task 收敛完成再断言。
        config_file.write_text(json.dumps({"mcpServers": {
            "b": {"command": "b", "args": ["--changed"]},
            "c": {"command": "c", "args": []},
        }}), encoding="utf-8")
        mcp_client.get_client_manager()
        settled = _wait_until(lambda: set(manager.session_map) == {"b", "c"}
                              and len(group.disconnected) == 2)
        assert settled, (f"后台收敛未完成: sessions={set(manager.session_map)}, "
                         f"disconnected={[s.server_info.name for s in group.disconnected]}")
        assert set(manager.session_map) == {"b", "c"}
        assert len(group.disconnected) == 2, \
            f"disconnect 未真正完成（被吞成日志了）: {[s.server_info.name for s in group.disconnected]}"
        for session in group.disconnected:
            assert group.disconnect_tasks[session] is group.connect_tasks[session], \
                f"{session.server_info.name}: disconnect 必须在 connect 的同一个 task 里"
    finally:
        _reset_worker()
