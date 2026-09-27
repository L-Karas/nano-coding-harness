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

# 建连完成后由 MCP loop 线程赋值：UI 取数走它（非阻塞），不参与建连触发
_ready_manager = None
# 放进 server 指令队列、让常驻 task 断连退出的哨兵（见 _manage_server）
_STOP = object()


def _sanitize(name: str) -> str:
    return _DISALLOWED_CHARS.sub("_", name)


def _tool_prefix(server_name: str) -> str:
    """mcp__<server>__ 前缀：清洗非法字符，避免 MCP 工具与内置工具重名。"""
    return f"mcp__{_sanitize(server_name)}__"


def _component_name(tool_name: str, server) -> str:
    """ClientSessionGroup 的工具命名钩子。"""
    return _tool_prefix(server.name) + _sanitize(tool_name)


def _tool_text(tool_result) -> str:
    """MCP tools currently return TextContent blocks only."""
    return "\n".join(block.text for block in tool_result.content if isinstance(block, TextContent))


class ClientManager:
    """一个 MCP 后台 loop 上的会话集合。

    每个 server 有一个常驻 task（_manage_server）独占它的 connect / disconnect：anyio 的
    cancel scope 要求进入与退出在同一 task。配置变更只把新配置排进该 server 的指令队列，
    由常驻 task 自己断旧连新，调用方不等待建连。
    """

    def __init__(self, server_configs: dict):
        self.server_configs = server_configs
        self.exit_stack = AsyncExitStack()
        self.session_group: ClientSessionGroup | None = None
        # config_server_name -> session
        self.session_map: dict[str, ClientSession] = {}
        # config_server_name -> connecting / connected / failed（/mcp 展示用）
        self.status: dict[str, str] = {}
        # config_server_name -> [{tool_name, tool_description}]（/mcp 展示用）
        self.tools_by_server: dict[str, list[dict]] = {}
        self.tool_handlers: dict = {}
        self.async_tool_handlers: dict = {}
        self._tools: list[dict] = []
        # 每个 server 的指令队列与常驻 task（key = 配置里的 server 名）
        self._server_commands: dict[str, asyncio.Queue] = {}
        self._server_tasks: dict[str, asyncio.Task] = {}

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

    def list_tools(self) -> list[dict]:
        """MCP tools in the OpenAI function schema：当前连接的工具快照（只读）。"""
        return self._tools

    def _refresh_tools(self) -> None:
        """按当前已连会话重建全部派生数据：handler、OpenAI schema、/mcp 快照。

        都是整表替换引用：别的线程读到的是完整旧表或完整新表，不会与后台的连接变化并发迭代。
        """
        handlers, async_handlers, schema, tools_by_server = {}, {}, [], {}
        for server_name, session in self.session_map.items():
            prefix = _tool_prefix(session.server_info.name)
            rows = []
            for tool_name, tool in self.session_group.tools.items():
                if not tool_name.startswith(prefix):
                    continue
                rows.append({"tool_name": tool_name[len(prefix):],
                             "tool_description": tool.description})
                schema.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                })
                handlers[tool_name] = lambda *, _name=tool_name, **kwargs: self.tool_call(_name, kwargs)
                async_handlers[tool_name] = (
                    lambda *, _name=tool_name, ctx=None, **kwargs: self.tool_call_async(_name, ctx, kwargs))
            tools_by_server[server_name] = rows
        self.tool_handlers, self.async_tool_handlers = handlers, async_handlers
        self._tools, self.tools_by_server = schema, tools_by_server

    def _register_session(self, server_name: str, session: ClientSession) -> None:
        """连接成功后登记会话：状态转 connected，工具表立刻带上它的工具。"""
        _LOGGER.info(f"[MCP] connect {server_name} success")
        self.status[server_name] = "connected"
        self.session_map[server_name] = session
        self._refresh_tools()

    async def _close_session(self, server_name: str) -> None:
        """断开并注销该 server 的会话（本来没有会话则为空操作）；随后移除它的工具。"""
        session = self.session_map.pop(server_name, None)
        if session is None:
            return
        try:
            await self.session_group.disconnect_from_server(session)
        except Exception as e:
            _LOGGER.error(f"[MCP] disconnect {server_name} failed: {e!r}")
        self._refresh_tools()

    async def _manage_server(self, server_name: str, config: dict, commands: asyncio.Queue,
                             ready: asyncio.Event) -> None:
        """该 server 的常驻 task：它的 connect / disconnect 只在本 task 内执行。

        ready 在首个连接结果（成功或失败）落地后置位，供 init 判断"首次尝试已结束"。

        ponytail: connect 不设超时 —— 挂死的 server 会停在 connecting，init（及 warmup 线程、
        /mcp 状态）会一直等它；调用方 get_client_manager 有 _MANAGER_WAIT_TIMEOUT 兜底。
        """
        try:
            while True:
                self.status[server_name] = "connecting"
                try:
                    session = await self.session_group.connect_to_server(
                        self._server_params(server_name, config))
                except Exception as e:
                    self.status[server_name] = "failed"
                    _LOGGER.error(f"[MCP] connect {server_name} failed: {e!r}")
                else:
                    self._register_session(server_name, session)
                ready.set()
                config = await commands.get()
                await self._close_session(server_name)  # 收到新配置 / 停止：先断旧
                if config is _STOP:
                    return
        finally:
            ready.set()  # 首个连接结果落地前就退出，也不能让 init 一直等
            if self._server_tasks.get(server_name) is asyncio.current_task():
                self._server_tasks.pop(server_name, None)  # 已被新 task 顶替时不删掉它的槽位

    def _start_server_task(self, server_name: str, config: dict) -> asyncio.Event:
        """为 server 建专属指令队列并启动常驻 task（只在 MCP loop 上调用）。

        队列随创建一起传入 task：删掉 server 又立刻重加时，旧 task 读的是旧队列，
        不会误收到新 task 的指令。
        """
        commands: asyncio.Queue = asyncio.Queue()
        ready = asyncio.Event()
        self._server_commands[server_name] = commands
        self.status[server_name] = "connecting"
        self._server_tasks[server_name] = asyncio.create_task(
            self._manage_server(server_name, config, commands, ready))
        return ready

    async def _connect_all_servers(self) -> None:
        self.session_group = await self.exit_stack.enter_async_context(
            ClientSessionGroup(component_name_hook=_component_name))

        # 每个 server 一个常驻 task，首连彼此并发；等各自的首次尝试落地，init 即视为就绪
        ready_events = [self._start_server_task(server_name, config)
                        for server_name, config in self.server_configs["mcpServers"].items()]
        await asyncio.gather(*(event.wait() for event in ready_events))

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

    def _apply_latest_config(self) -> None:
        """读盘 + 校验 + 同步配置；只在 MCP loop 上调用（无 await，天然串行不打架）。"""
        if self.session_group is None:  # 尚未建连完成 / 已关闭：init 或下次调用会兜底
            return
        try:
            configs = _read_config()
            _validate_config(configs)
        except Exception as e:
            _LOGGER.warning(f"[MCP] config reload skipped, keeping current servers: {e!r}")
            return
        self._apply_config(configs)

    def _apply_config(self, new_server_configs: dict) -> None:
        """对比新旧配置，把新增 / 改配置 / 删除交给各 server 的常驻 task（不等待建连）。"""
        old_configs = self.server_configs.get("mcpServers", {})
        new_configs = new_server_configs.get("mcpServers", {})
        if old_configs == new_configs:
            return
        for server_name in new_configs.keys() - old_configs.keys():
            _LOGGER.info(f"[MCP] connect {server_name}")
            self._start_server_task(server_name, new_configs[server_name])
        for server_name in old_configs.keys() & new_configs.keys():
            if old_configs[server_name] == new_configs[server_name]:
                continue
            _LOGGER.info(f"[MCP] update {server_name} config")
            self.status[server_name] = "connecting"  # 排队即置状态，UI 不显示旧连接的 connected
            self._server_commands[server_name].put_nowait(new_configs[server_name])
        for server_name in old_configs.keys() - new_configs.keys():
            _LOGGER.info(f"[MCP] disconnect {server_name}")
            if commands := self._server_commands.pop(server_name, None):
                commands.put_nowait(_STOP)
            self.status.pop(server_name, None)
        self.server_configs = new_server_configs

    async def aclose(self) -> None:
        for commands in self._server_commands.values():
            commands.put_nowait(_STOP)
        if self._server_tasks:
            await asyncio.gather(*self._server_tasks.values(), return_exceptions=True)

        self.session_map = {}
        self.status = {}
        self.tools_by_server = {}
        self.tool_handlers = {}
        self.async_tool_handlers = {}
        self._tools = []
        self._server_commands = {}
        self._server_tasks = {}

        await self.exit_stack.aclose()
        self.session_group = None

    @classmethod
    async def init_client_manager(cls, server_configs: dict) -> "ClientManager":
        global _ready_manager
        manager = cls(server_configs)
        try:
            await manager._connect_all_servers()
            manager._apply_latest_config()  # 兜底：init 期间落盘的配置变更也能生效
            _ready_manager = manager  # 建连就绪：UI 取数走 peek_client_manager
        except Exception:
            await manager.aclose()
            raise
        return manager


def get_mcp_server_list() -> dict[str, dict]:
    """
    MCP server snapshot for the /mcp UI (non-blocking).

    Returns:
        dict: {server_name: {"status": "connecting|connected|failed", "tools": [
              {"tool_name": "...", "tool_description": "..."}]}}

    行以配置文件为准（增删立即反映），status / tools 来自后台 loop 维护的快照。
    """
    manager = peek_client_manager()
    if manager is None:
        raise RuntimeError("MCP still connecting")
    return {
        server_name: {
            "status": manager.status.get(server_name, "connecting"),
            "tools": manager.tools_by_server.get(server_name, []),
        }
        for server_name in _read_config().get("mcpServers", {})
    }


def _validate_config(configs: dict):
    """Validate mcp server config format"""
    if not configs:
        raise ValueError("MCP server config empty")
    if not isinstance(configs, dict):
        _LOGGER.warning(f"MCP server config type invalid: {type(configs)}")
        raise ValueError("MCP server config invalid")
    servers = configs.get("mcpServers")
    if not isinstance(servers, dict):
        _LOGGER.warning(f"MCP server config type invalid: {type(servers)}")
        raise ValueError("MCP server config invalid")
    for server_name, config in servers.items():
        # 与 _server_params 支持的两种传输形态一致：stdio（command + args 两项）
        # 或 url（type 为 sse / streamable_http）
        valid = isinstance(config, dict) and len(config) == 2 and (
                ("command" in config and "args" in config)
                or ("url" in config and config.get("type") in ("sse", "streamable_http")))
        if not valid:
            _LOGGER.warning(f"MCP server config type invalid: {server_name}={config}")
            raise ValueError("MCP server config invalid")


def _reload_config_in_background() -> None:
    """配置已落盘：请已建连的 manager 在 MCP loop 上重读；未就绪时 init 会直接读到新文件。"""
    if manager := peek_client_manager():
        _get_loop().call_soon_threadsafe(manager._apply_latest_config)


def configure_mcp_server(mcp_configs: str) -> tuple[bool, str]:
    """
    Configure MCP Servers
    Args:
        mcp_configs:

    Returns:
        tuple[whether configure success, configure information]
    """
    try:
        new_servers = json.loads(mcp_configs)
        _validate_config(new_servers)
        configs = _read_config()
        configs["mcpServers"] = configs.get("mcpServers", {}) | new_servers["mcpServers"]
        MCP_CONFIG_FILE.write_text(json.dumps(configs, ensure_ascii=False, indent=4), encoding="utf-8")
        _reload_config_in_background()  # 落盘即返回，连接在 MCP loop 后台完成
        return True, "Configured MCP Servers"
    except Exception as e:
        _LOGGER.warning(f"[MCP] server config invalid: {e}")
        return False, str(e)


def unconfigure_mcp_server(mcp_server_name: str) -> tuple[bool, str]:
    """
    Unconfigure an MCP Server
    Args:
        mcp_server_name:

    Returns:
        tuple[whether unconfigure success, unconfigure information]
    """
    mcp_configs = _read_config()
    if mcp_server_name not in mcp_configs.get("mcpServers", {}):
        return False, f"MCP server {mcp_server_name} not found"
    mcp_configs["mcpServers"].pop(mcp_server_name)
    try:
        MCP_CONFIG_FILE.write_text(json.dumps(mcp_configs, ensure_ascii=False, indent=4), encoding="utf-8")
        _reload_config_in_background()  # 落盘即返回，断开在 MCP loop 后台完成
        return True, "Unconfigured MCP Servers"
    except Exception as e:
        _LOGGER.warning(f"[MCP] unconfigure server error: {e}")
        return False, f"Unconfigure mcp server error: {e}"


def _read_config() -> dict:
    if not MCP_CONFIG_FILE.exists():
        raise FileNotFoundError(f"MCP server config file not found at {MCP_CONFIG_FILE}")
    return json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))


async def aget_client_manager() -> ClientManager:
    server_configs = _read_config()
    _validate_config(server_configs)
    return await ClientManager.init_client_manager(server_configs)


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


def peek_client_manager() -> ClientManager | None:
    """非阻塞取已就绪的 manager（UI 取数用）；建连未完成/失败时返回 None。"""
    return _ready_manager


def _reset_manager_future() -> None:
    global _manager_future, _ready_manager
    with _manager_lock:
        _manager_future = None
    _ready_manager = None


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
    配置变化不再同步等待：交给 MCP loop 后台处理，本轮用当前已连工具。
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
        _LOGGER.warning("[MCP] MCP manager still connecting, falling back to builtin tools this round")
        raise RuntimeError(
            "MCP manager still connecting, falling back to builtin tools this round"
        ) from None
    except Exception:
        _reset_manager_future()  # 建连失败：清缓存，下次调用重试
        raise

    # 已就绪：配置变化交给 MCP loop 后台处理（立即返回），本轮先用当前已连上的工具
    _reload_config_in_background()
    return manager


def warmup() -> None:
    """阻塞直到建连完成或失败；由 main.py 在 daemon 线程调用，使首个 agent 轮次通常已就绪。"""
    try:
        _ensure_manager_started().result()
    except Exception as e:
        _LOGGER.error(f"[MCP] warmup failed: {e!r}")
