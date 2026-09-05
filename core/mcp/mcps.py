"""
MCP System

MCP is modeled as late-bound tools: 
Connect first, then discovered servertools are merged into
 the normal tool pool with mcp__server__tool names.

mcp config file format:
{
    "mcpServers": {
        "local-server-name": {
            "command": "...",
            "args": [
                ...
            ]
        },
        "remote-server-name": {
            "type": "...",
            "url": "..."
        },
        ....
    }
}
"""
import asyncio
import json
import re
import threading
import time
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Optional

from mcp import StdioServerParameters, ClientSessionGroup, ClientSession
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters
from mcp_types import TextContent

from core.config import MCP_CONFIG_FILE
from core.log.log import get_logger

_LOGER = get_logger(__name__)

_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_loop: Optional[asyncio.AbstractEventLoop] = None
_manager_future: Optional[Future["ClientManager"]] = None
_manager_lock = threading.Lock()
# 有界等待策略：一次调用最多阻塞 _MANAGER_WAIT_TIMEOUT，且每个 _RETRY_INTERVAL 窗口内至多一次；
# 慢速建连只会拖住一个 agent 轮次一次，不会每轮都卡。
_MANAGER_WAIT_TIMEOUT = 5.0
_RETRY_INTERVAL = 30.0
_last_attempt_at = 0.0


class ClientManager:
    def __init__(self, server_configs: dict):
        self.server_configs = server_configs
        self.session_map: dict[str, ClientSession] = {}
        self.exit_stack: AsyncExitStack = AsyncExitStack()
        self.session_group: ClientSessionGroup | None = None
        self.tool_handlers: dict = {}
        self.tool_list: list = []

    # todo: only support tools returned text content
    def tool_call(self, tool_name: str, tool_args: Optional[dict] = None) -> str:
        if not self.tool_handlers.get(tool_name):
            return f"Tool '{tool_name}' not found"

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.session_group.call_tool(tool_name, tool_args), _get_loop()
            )
            tool_result = future.result(timeout=30)
            return "\n".join(
                block.text
                for block in tool_result.content if isinstance(block, TextContent)
            )
        except Exception as e:
            return str(e)

    # todo: only support openai api tool format
    def list_tools(self, tool_schema_type: str = "openai") -> list[dict]:
        if not self.tool_list:
            if tool_schema_type == "openai":
                self.tool_list = [{
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    }
                } for tool_name, tool in self.session_group.tools.items()]

        return self.tool_list

    async def _init_tool_handlers(self):
        for tool_name, tool in self.session_group.tools.items():
            self.tool_handlers[tool_name] = (
                lambda *, name=tool_name, **kwargs: self.tool_call(name, kwargs)
            )

    async def _connect_to_servers(self):
        """
        Connect to the servers.
        """
        # 对 mcp server 工具名进行处理，防止工具名冲突
        name_fn = lambda name, server_info: \
            f"mcp__{_DISALLOWED_CHARS.sub('_', server_info.name)}__{_DISALLOWED_CHARS.sub('_', name)}"
        self.session_group = await self.exit_stack.enter_async_context(
            ClientSessionGroup(component_name_hook=name_fn)
        )

        for server_name, server_config in self.server_configs["mcpServers"].items():
            try:
                server_type = self._validate_server_config(server_name, server_config)
                if server_type == "stdio":
                    server_params = StdioServerParameters(command=server_config["command"], args=server_config["args"])
                elif server_type == "sse":
                    server_params = SseServerParameters(url=server_config["url"])
                else:
                    server_params = StreamableHttpParameters(url=server_config["url"])
                session = await self.session_group.connect_to_server(server_params)
                self.session_map[server_name] = session
            except Exception as e:
                _LOGER.warning(f"[MCP] connect {server_name} failed: {e}")

    async def aclose(self):
        await self.exit_stack.aclose()
        self.session_group = None
        self.tool_handlers = {}
        self.session_map = {}
        self.tool_list = []

    @staticmethod
    def _validate_server_config(server_name: str, server_config: dict) -> str:
        """
        Validate the server config. Return mcp server type ('sse', 'streamable_http', 'stdio')
        """
        if not isinstance(server_config, dict):
            raise ValueError(f"MCP server '{server_name}' config type invalid: {type(server_config)}")

        if "command" in server_config and server_config.get("command"):
            return "stdio"

        if "type" in server_config:
            if not server_config.get("type") or server_config["type"] not in ("sse", "streamable_http"):
                raise ValueError(f"MCP server '{server_name}' type invalid: {server_config['type']}")
            return server_config["type"]

        raise ValueError("MCP server config invalid")

    @classmethod
    async def init_client_manager(cls, server_configs: dict) -> "ClientManager":
        client_manager = cls(server_configs)
        try:
            await client_manager._connect_to_servers()
            await client_manager._init_tool_handlers()
        except Exception:
            await client_manager.aclose()
            raise
        return client_manager


async def _update_client_manager(client_manager: ClientManager) -> ClientManager:
    """配置有变化则重建 manager,无变化返回原实例。"""
    if not MCP_CONFIG_FILE.exists():
        raise FileNotFoundError(f"MCP server config file not found at {MCP_CONFIG_FILE}")

    new_configs = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8").strip())

    if new_configs == client_manager.server_configs:
        # 释放old client manager资源
        return client_manager

    await client_manager.aclose()
    return await ClientManager.init_client_manager(new_configs)


async def aget_client_manager() -> ClientManager:
    if not MCP_CONFIG_FILE.exists():
        raise FileNotFoundError(f"MCP server config file not found at {MCP_CONFIG_FILE}")

    config_content = MCP_CONFIG_FILE.read_text(encoding="utf-8").strip()
    server_configs = json.loads(config_content)

    if "mcpServers" not in server_configs:
        raise ValueError(f"MCP server config invalid: {server_configs}")

    return await ClientManager.init_client_manager(server_configs)


def _get_loop() -> asyncio.AbstractEventLoop:
    """
    Long-lived event loop in a daemon thread that owns all MCP sessions.
    A fresh asyncio.run() per call would deadlock on teardown: the loop closes
    while stdio/HTTP transports are still open, and cancelling them never
    completes (the spawned subprocess keeps the pipe alive).
    """
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, name="mcp-event-loop", daemon=True).start()
    return _loop


def _kick_manager() -> None:
    """Create the singleton init future if absent. Thread-safe, idempotent."""
    global _manager_future
    if _manager_future is None:
        with _manager_lock:
            if _manager_future is None:
                _manager_future = asyncio.run_coroutine_threadsafe(
                    aget_client_manager(), _get_loop()
                )


def get_client_manager() -> ClientManager:
    """
    Lazily build and cache the singleton ClientManager on the background loop.

    调用方永远不会被无限期卡住：首次获取最多等 _MANAGER_WAIT_TIMEOUT，且每个
    _RETRY_INTERVAL 窗口内至多等一次；建连仍未完成时抛 RuntimeError（由调用方
    记录日志并回退到内置工具）——工具池每轮都会重组装，下一轮建连完成自然带上 MCP 工具。
    run_coroutine_threadsafe returns a Future that re-raises the coroutine's
    exception on .result(), so a failed init clears the cache and the next
    call retries. A timed-out wait keeps the pending future (connect continues
    in the background) and clears nothing.
    """
    global _manager_future, _last_attempt_at

    _kick_manager()

    with _manager_lock:
        wait = 0.0 if time.monotonic() - _last_attempt_at < _RETRY_INTERVAL else _MANAGER_WAIT_TIMEOUT

    try:
        manager = _manager_future.result(timeout=wait)
    except TimeoutError:
        # 建连仍在后台进行：保留 pending future，不清理缓存；
        # 刚等过一轮，窗口内后续轮次不再等，避免每轮都被拖住
        with _manager_lock:
            _last_attempt_at = time.monotonic()
        raise RuntimeError(
            "MCP manager still connecting, falling back to builtin tools this round"
        ) from None
    except Exception:
        # 建连本身失败：清缓存，下次调用重试
        with _manager_lock:
            _manager_future = None
        raise

    # Ready: rebuild only when the config file changed.
    try:
        _manager_future = asyncio.run_coroutine_threadsafe(
            _update_client_manager(manager), _get_loop()
        )
        return _manager_future.result()
    except Exception:
        with _manager_lock:
            _manager_future = None
        raise


def warmup() -> None:
    """
    阻塞直至 MCP 建连完成或失败；由调用方（main.py）在 daemon 线程里执行，
    使首个 agent 轮次通常已就绪。
    """
    _kick_manager()
    try:
        _manager_future.result()
    except Exception as e:
        _LOGER.warning(f"[MCP] warmup failed: {e}")


if __name__ == '__main__':

    async def main():
        client_manager = await aget_client_manager()

        print("Available tools:\n")
        for tool_schema in client_manager.list_tools():
            print("-" * 10)
            print(f"Tool name: {tool_schema['function']['name']}")
            print(f"Tool description: {tool_schema['function']['description']}\n")
            print("-" * 10)

        await client_manager.aclose()


    asyncio.run(main(), debug=True)
