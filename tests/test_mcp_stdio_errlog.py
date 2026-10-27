"""回归：stdio server 的 stderr 不能是 mcp import 时绑定的 sys.stderr。

现场：Textual 运行期把 sys.stderr 换成 `_PrintCapture`（`fileno()` 返回 -1）。mcp 的
`stdio_client(server, errlog=sys.stderr)` 是默认参数，import 那一刻就绑死；若 import 落在 TUI
启动之后，建连时 Popen 在 Windows 上拿 -1 调 msvcrt.get_osfhandle，抛
`OSError(9, 'Bad file descriptor')` —— 所有 stdio server 连不上，HTTP 传输照常。
"""
import asyncio
import contextlib
import sys

import mcp

import core.mcp.mcp_client as mcp_client


class _Capture:
    """textual.app._PrintCapture 的最小形态：能写、能 flush，但 fileno 是 -1。"""

    def write(self, text: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return -1


def _fake_stdio_client(seen: dict):
    """替身工厂：记下收到的 errlog，并像真的一样给出 (read_stream, write_stream) 二元组。"""

    @contextlib.asynccontextmanager
    async def fake(server, errlog=None):
        seen["errlog"] = errlog
        yield "read", "write"

    return fake


def test_stdio_client_never_inherits_captured_stderr(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(mcp_client._mcp_stdio, "stdio_client", _fake_stdio_client(seen))
    monkeypatch.setattr(mcp_client, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sys, "stderr", _Capture())

    server = mcp_client.ClientManager._server_params("s", {"command": "x"})

    async def call():
        async with mcp.stdio_client(server) as (read, write):
            assert (read, write) == ("read", "write"), "必须原样透传 (read_stream, write_stream)"
            assert seen["errlog"] is not sys.stderr, "errlog 不能是运行期的 sys.stderr 捕获对象"
            assert seen["errlog"].fileno() >= 0, "-1 在 Windows 上就是 EBADF"

    asyncio.run(call())
    assert (tmp_path / "mcp-stderr.log").exists(), "stdio server 的 stderr 应落盘"


def test_stdio_client_survives_unwritable_errlog(monkeypatch, tmp_path):
    """日志落盘失败只该丢 stderr，不该把「可连」变成「连不上」。"""
    seen = {}
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")  # LOG_DIR 位置被文件占住 → mkdir 抛 OSError
    monkeypatch.setattr(mcp_client._mcp_stdio, "stdio_client", _fake_stdio_client(seen))
    monkeypatch.setattr(mcp_client, "LOG_DIR", blocker)

    server = mcp_client.ClientManager._server_params("s", {"command": "x"})

    async def call():
        async with mcp.stdio_client(server) as (read, write):
            assert (read, write) == ("read", "write")
            assert seen["errlog"].fileno() >= 0

    asyncio.run(call())


def test_session_group_resolves_the_patched_client():
    """SDK 在 _establish_session 里按运行期属性查 `mcp.stdio_client`；改成直接 import 要炸在这里。"""
    from mcp.client.session_group import ClientSessionGroup

    assert ClientSessionGroup._establish_session.__globals__["mcp"] is mcp
