"""
MCP System

MCP is modeled as late-bound tools: connect first, then discovered server
tools are merged into the normal tool pool with mcp__server__tool names.
"""
import asyncio
import json
import re
import threading
import time
from concurrent.futures import Future
from contextlib import AsyncExitStack

from mcp import StdioServerParameters, ClientSessionGroup, ClientSession
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters
from mcp_types import TextContent

from core.config import MCP_CONFIG_FILE
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

# 有界等待策略：一次调用最多阻塞 _MANAGER_WAIT_TIMEOUT，且每个 _RETRY_INTERVAL 窗口内至多一次；
# 慢速建连只会拖住一个 agent 轮次一次，不会每轮都卡。
_MANAGER_WAIT_TIMEOUT = 5.0
_RETRY_INTERVAL = 30.0
_TOOL_CALL_TIMEOUT = 60.0

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_manager_future: Future | None = None
_manager_lock = threading.Lock()
_last_attempt_at = 0.0


def _sanitize(name: str) -> str:
    return _DISALLOWED_CHARS.sub("_", name)


def _tool_text(tool_result) -> str:
    """MCP tools currently return TextContent blocks only."""
    return "\n".join(block.text for block in tool_result.content if isinstance(block, TextContent))


class ClientManager:
    def __init__(self, server_configs: dict):
        self.server_configs = server_configs
        self.exit_stack = AsyncExitStack()
        self.session_group: ClientSessionGroup | None = None
        # config_server_name -> session
        self.session_map: dict[str, ClientSession] = {}
        # server_info_name -> config_server_name
        self.server_to_config: dict[str, str] = {}
        self.current_server_info: dict = {}
        self.tool_handlers: dict = {}
        self.async_tool_handlers: dict = {}

    def _submit(self, tool_name: str, tool_args: dict | None) -> Future:
        """调度回拥有 MCP 会话的后台 loop 上执行（本 loop 只 await 结果，取消也能传出）。"""
        return asyncio.run_coroutine_threadsafe(
            self.session_group.call_tool(tool_name, tool_args), _get_loop())

    def tool_call(self, tool_name: str, tool_args: dict | None = None) -> str:
        if tool_name not in self.tool_handlers:
            return f"Tool '{tool_name}' not found"
        try:
            return _tool_text(self._submit(tool_name, tool_args).result(_TOOL_CALL_TIMEOUT))
        except Exception as e:
            return str(e)

    async def tool_call_async(self, tool_name: str, ctx=None, tool_args: dict | None = None) -> str:
        if tool_name not in self.tool_handlers:
            return f"Tool '{tool_name}' not found"

        if ctx:
            ctx.raise_if_cancelled()

        try:
            call = asyncio.wrap_future(self._submit(tool_name, tool_args))
            text = _tool_text(await asyncio.wait_for(call, _TOOL_CALL_TIMEOUT))
            _LOGGER.info(f"[MCP] {tool_name} -> {text[:50]}")
            return text
        except Exception as e:  # CancelledError 是 BaseException，不会被这里吞掉
            return str(e)

    def list_server_info(self) -> dict:
        if self.current_server_info:
            return self.current_server_info

        server_info = {}
        for server_name, session in self.session_map.items():
            server_info[server_name] = [
            ]

    def list_tools(self) -> list[dict]:
        """MCP tools in the OpenAI function schema (the only format used so far)."""
        return [{
            "type": "function",
            "function": {
                "name": name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        } for name, tool in self.session_group.tools.items()]

    def _init_tool_handlers(self) -> None:
        for name in self.session_group.tools:
            self.tool_handlers[name] = (
                lambda *, _name=name, **kwargs: self.tool_call(_name, kwargs))
            self.async_tool_handlers[name] = (
                lambda *, _name=name, ctx=None, **kwargs: self.tool_call_async(_name, ctx, kwargs))

    async def _connect_to_servers(self) -> None:
        # 工具名统一加 mcp__server__ 前缀并清洗非法字符，避免与内置工具重名
        name_hook = lambda tool_name, server: f"mcp__{_sanitize(server.name)}__{_sanitize(tool_name)}"
        self.session_group = await self.exit_stack.enter_async_context(
            ClientSessionGroup(component_name_hook=name_hook))

        connects = []
        connect_server_names = []
        for server_name, config in self.server_configs["mcpServers"].items():
            try:
                connects.append(self.session_group.connect_to_server(self._server_params(server_name, config)))
                connect_server_names.append(server_name)
            except Exception as e:
                _LOGGER.error(f"[MCP] connect {server_name} failed: {e!r}")
                continue

        # concurrent connect servers
        results = await asyncio.gather(*connects, return_exceptions=True)
        for server_name, res in zip(connect_server_names, results):
            if isinstance(res, Exception):
                _LOGGER.error(f"[MCP] connect failed: {res!r}")
            elif isinstance(res, ClientSession):
                _LOGGER.info(f"[MCP] connect {server_name} success")
                self.server_to_config[res.server_info.name] = server_name
                self.session_map[server_name] = res

    @staticmethod
    def _server_params(server_name: str, config: dict):
        """Build the transport parameters for one mcpServers entry."""
        if not isinstance(config, dict):
            raise ValueError(f"MCP server '{server_name}' config type invalid: {type(config)}")
        if config.get("command"):
            return StdioServerParameters(command=config["command"], args=config.get("args", []))
        transport = config.get("type")
        if transport == "sse":
            return SseServerParameters(url=config["url"])
        if transport == "streamable_http":
            return StreamableHttpParameters(url=config["url"])
        raise ValueError(f"MCP server '{server_name}' type invalid: {transport!r}")

    async def update_client_manager(self, new_server_configs: dict) -> "ClientManager":
        """
        Update the MCP client manager with new server configs.
        Connect new servers, disconnect removed servers and update existing servers.
        Args:
            new_server_configs:

        Returns:

        """
        # merge old and new config
        old_configs, new_configs = self.server_configs["mcpServers"], new_server_configs["mcpServers"]
        all_configs = old_configs | new_configs

        updates = []
        update_server_names = []
        for server_name, config in all_configs.items():
            # 4 Conditions:
            # 1. server_name in old_configs and server_name in new_configs:
            # 1.1  new_configs[server_name] == old_configs[server_name] -> continue
            # 1.2  new_configs[server_name] != old_configs[server_name] -> update
            # 2. server_name in old_configs and server_name not in new_configs -> disconnect
            # 3. server_name not in old_configs and server_name in new_configs -> connect
            try:
                if server_name in old_configs and server_name in new_configs:
                    if old_configs[server_name] != new_configs[server_name]:
                        # update an existing server
                        _LOGGER.info(f"[MCP] update {server_name} config")
                        updates.extend([
                            self.session_group.disconnect_from_server(self.session_map[server_name]),
                            self.session_group.connect_to_server(self._server_params(server_name, config))
                        ])
                        update_server_names.extend([server_name] * 2)
                        self.session_map.pop(server_name)
                elif server_name in old_configs and server_name not in new_configs:
                    # disconnect an existing server
                    _LOGGER.info(f"[MCP] disconnect {server_name}")
                    updates.append(self.session_group.disconnect_from_server(self.session_map[server_name]))
                    update_server_names.append(server_name)
                    self.session_map.pop(server_name)
                elif server_name not in old_configs and server_name in new_configs:
                    # connect a new server
                    _LOGGER.info(f"[MCP] connect {server_name}")
                    updates.append(self.session_group.connect_to_server(
                        self._server_params(server_name, new_configs[server_name])))
                    update_server_names.append(server_name)

            except Exception as e:
                _LOGGER.error(f"[MCP] update {server_name} failed: {e!r}")

        # concurrent update servers
        update_results = await asyncio.gather(*updates, return_exceptions=True)
        for server_name, res in zip(update_server_names, update_results):
            if isinstance(res, Exception):
                _LOGGER.error(f"[MCP] {server_name} error: {res!r}")
            elif isinstance(res, ClientSession):
                _LOGGER.info(f"[MCP] connect {server_name} success")
                self.server_to_config[res.server_info.name] = server_name
                self.session_map[server_name] = res

        self.server_configs = new_server_configs

        return self

    async def aclose(self) -> None:
        self.server_to_config = {}
        self.session_map = {}
        self.tool_handlers = {}
        self.async_tool_handlers = {}
        self.current_server_info = {}

        await self.exit_stack.aclose()
        self.session_group = None

    @classmethod
    async def init_client_manager(cls, server_configs: dict) -> "ClientManager":
        manager = cls(server_configs)
        try:
            await manager._connect_to_servers()
            manager._init_tool_handlers()
        except Exception:
            await manager.aclose()
            raise
        return manager


def get_mcp_server_list() -> dict:
    """
    Get MCP server list
    Returns:
        dict: {"server_name": [{"tool_name": "...", "tool_description"}...],...}
    """
    client_manager = get_client_manager()
    tools = client_manager.list_tools()
    server_info = {}
    for tool in tools:
        _, server_info_name, tool_name = tool["function"]["name"].split("__")
        tool_description = tool["function"]["description"]
        server_name = client_manager.server_to_config[server_info_name]
        if server_name not in server_info:
            server_info[server_name] = []
        server_info[server_name].append({"tool_name": tool_name, "tool_description": tool_description})

    return server_info


def _validate_config(configs: dict):
    """Validate mcp server config format"""
    if not configs:
        raise ValueError("MCP server config empty")
    if not isinstance(configs, dict):
        _LOGGER.warning(f"MCP server config type invalid: {type(configs)}")
        raise ValueError(f"MCP server config invalid")
    if "mcpServers" not in configs:
        raise ValueError(f"MCP server config invalid")
    if not isinstance(configs["mcpServers"], dict):
        _LOGGER.warning(f"MCP server config type invalid: {type(configs['mcpServers'])}")
        raise ValueError(f"MCP server config invalid")
    for server_name, config in configs["mcpServers"].items():
        # 与 _server_params 支持的两种传输形态一致：stdio（command + args 两项）
        # 或 url（type 为 sse / streamable_http）
        valid = isinstance(config, dict) and len(config) == 2 and (
                ("command" in config and "args" in config)
                or ("url" in config and config.get("type") in ("sse", "streamable_http")))
        if not valid:
            _LOGGER.warning(f"MCP server config type invalid: {server_name}={config}")
            raise ValueError(f"MCP server config invalid")


def configure_mcp_server(mcp_configs: str) -> tuple[bool, str]:
    """
    Configure MCP Servers
    Args:
        mcp_configs:

    Returns:
        tuple[whether configure success, configure information]
    """
    global _loop
    try:
        mcp_configs = json.loads(mcp_configs)
        _validate_config(mcp_configs)
        mcp_configs["mcpServers"] = _read_config().get("mcpServers", {}) | mcp_configs["mcpServers"]
        # update client
        with open(MCP_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(mcp_configs, ensure_ascii=False, indent=4))
        get_client_manager()
        return True, "Configured MCP Servers"
    except Exception as e:
        _LOGGER.warning(f"MCP server config invalid: {e}")
        return False, str(e)


def unconfigure_mcp_server(mcp_server_name: str) -> tuple[bool, str]:
    """
    Unconfigure an MCP Server
    Args:
        mcp_server_name:

    Returns:
        tuple[whether unconfigure success, unconfigure information]
    """
    global _loop

    mcp_configs = _read_config()
    if mcp_server_name not in mcp_configs.get("mcpServers", {}):
        return False, f"MCP server {mcp_server_name} not found"
    mcp_configs["mcpServers"].pop(mcp_server_name)
    try:
        # update client
        with open(MCP_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(mcp_configs, ensure_ascii=False, indent=4))
        get_client_manager()
        return True, "Unconfigured MCP Servers"
    except Exception as e:
        _LOGGER.warning(f"Unconfigure mcp server error: {e}")
        return False, f"Unconfigure mcp server error: {e}"


def _read_config() -> dict:
    if not MCP_CONFIG_FILE.exists():
        raise FileNotFoundError(f"MCP server config file not found at {MCP_CONFIG_FILE}")
    return json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))


async def aget_client_manager() -> ClientManager:
    server_configs = _read_config()
    _validate_config(server_configs)
    return await ClientManager.init_client_manager(server_configs)


async def _update_client_manager(manager: ClientManager) -> ClientManager:
    """配置有变化则重建，无变化返回原实例。"""
    server_configs = _read_config()
    _validate_config(server_configs)
    if server_configs.get("mcpServers") == manager.server_configs.get("mcpServers"):
        _LOGGER.info("[MCP] Server config remains unchanged. Return original manager")
        return manager

    return await manager.update_client_manager(server_configs)


def _get_loop() -> asyncio.AbstractEventLoop:
    """
    Long-lived event loop in a daemon thread that owns all MCP sessions.
    A fresh asyncio.run() per call would deadlock on teardown: the loop closes
    while stdio/HTTP transports are still open, and cancelling them never
    completes (the spawned subprocess keeps the pipe alive).
    """
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="mcp-event-loop", daemon=True).start()
        return _loop


def _reset_manager_future() -> None:
    global _manager_future
    with _manager_lock:
        _manager_future = None


def _ensure_manager_started() -> Future:
    """Start the singleton init coroutine once, on the MCP loop."""
    global _manager_future
    with _manager_lock:
        if _manager_future is None:
            _manager_future = asyncio.run_coroutine_threadsafe(aget_client_manager(), _get_loop())
        return _manager_future


def get_client_manager() -> ClientManager:
    """
    Lazily build and cache the singleton ClientManager on the background loop.

    调用方不会被无限期卡住：首次获取最多等 _MANAGER_WAIT_TIMEOUT，且每个
    _RETRY_INTERVAL 窗口内至多等一次；建连仍未完成时抛 RuntimeError（由调用方
    记录日志并回退到内置工具）——工具池每轮都会重组装，下一轮建连完成自然带上 MCP 工具。
    """
    global _last_attempt_at

    future = _ensure_manager_started()
    with _manager_lock:
        wait = 0.0 if time.monotonic() - _last_attempt_at < _RETRY_INTERVAL else _MANAGER_WAIT_TIMEOUT
    try:
        manager = future.result(wait)
    except TimeoutError:
        # 建连仍在后台进行：保留 pending future；刚等过一轮，窗口内后续轮次不再等
        with _manager_lock:
            _last_attempt_at = time.monotonic()
        _LOGGER.warning(f"[MCP] MCP manager still connecting, falling back to builtin tools this round")
        # raise RuntimeError(
        #     "MCP manager still connecting, falling back to builtin tools this round"
        # ) from None
    except Exception:
        _reset_manager_future()  # 建连失败：清缓存，下次调用重试
        raise

    # 已就绪：配置有变化则重建，否则沿用原实例
    try:
        return asyncio.run_coroutine_threadsafe(
            _update_client_manager(manager), _get_loop()
        ).result()
    except Exception:
        _reset_manager_future()
        raise


def warmup() -> None:
    """阻塞直到建连完成或失败；由 main.py 在 daemon 线程调用，使首个 agent 轮次通常已就绪。"""
    try:
        _ensure_manager_started().result()
    except Exception as e:
        _LOGGER.error(f"[MCP] warmup failed: {e!r}")


if __name__ == '__main__':
    fetch_server = {
        "mcpServers": {
            "fetch": {
                "type": "streamable_http",
                "url": "https://mcp.api-inference.modelscope.net/baefb50094ae45/mcp"
            }
        }
    }


    async def main():
        client_manager = await aget_client_manager()
        ser_info_list = get_mcp_server_list()
        configure_mcp_server(json.dumps(fetch_server))
        unconfigure_mcp_server("fetch")
        print("stop")


    asyncio.run(main())
