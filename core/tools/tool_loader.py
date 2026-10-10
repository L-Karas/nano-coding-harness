from typing import Any, Literal, Mapping

from core.log import get_logger
from core.mcp import get_client_manager
from core.runtime_context import ToolContext
from core.tools.base_tools import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类
from core.tools.extra_tools import *  # noqa: F401  # 导入扩展工具类以注册 BaseTool 子类
from core.tools.tool_base import BaseTool
from core.tools.tool_result import ToolResult
from core.tools.utils import _camel_to_snake
from core.tools.web_search import *  # noqa: F401  # 导入 web 工具类以注册 BaseTool 子类

_LOGGER = get_logger(__name__)
_TOOLS: dict[str, type[BaseTool]] = {}


def _validate_tool_class(cls: type[BaseTool]) -> None:
    """工具至少覆写 run / arun 之一；两个都靠基类缺省会在运行期互相递归。"""
    if cls.run is BaseTool.run and cls.arun is BaseTool.arun:
        raise RuntimeError(f"Tool {cls.__name__} implements neither run() nor arun()")


def _register_tools() -> None:
    """Index every BaseTool subclass by snake_case tool name (read_file) — the name callers pass in."""
    for cls in BaseTool.__subclasses__():
        _validate_tool_class(cls)
        name = _camel_to_snake(cls.__name__)
        if name in _TOOLS:
            raise RuntimeError(f"Duplicate tool name '{name}': {cls.__name__} vs {_TOOLS[name].__name__}")
        _TOOLS[name] = cls


_register_tools()


class _McpEntry:
    """MCP 工具的 pool 条目：schema + 同步 / 异步 handler，由装配处适配。"""

    def __init__(self, schema: dict, sync_handler, async_handler):
        self.schema = schema
        self._sync_handler = sync_handler
        self._async_handler = async_handler

    def execute_sync(self, args: dict, tctx: ToolContext | None) -> str:
        return str(self._sync_handler(**args))

    async def execute_async(self, args: dict, tctx: ToolContext | None) -> str:
        return str(await self._async_handler(ctx=tctx.agent_run if tctx else None, **args))


class ToolPool:
    """装配好的一组 Tool：取 schema 与执行调用的唯一入口。"""

    def __init__(self, entries: Mapping[str, type[BaseTool] | _McpEntry]):
        self._entries: dict[str, type[BaseTool] | _McpEntry] = dict(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def schemas(self) -> list[dict[str, Any]]:
        return [entry.to_openai_tool() if isinstance(entry, type) else entry.schema
                for entry in self._entries.values()]

    def has(self, name: str) -> bool:
        return name in self._entries

    def has_native_async(self, name: str) -> bool:
        """该工具是否有原生异步实现（缺省 arun 仍是同步实现，不应挂到事件循环上跑）。"""
        entry = self._entries.get(name)
        if entry is None:
            return False
        if not isinstance(entry, type):
            return True
        return entry.arun is not BaseTool.arun

    async def execute(self, name: str, args: dict, tctx: ToolContext | None = None) -> ToolResult:
        entry = self._entries.get(name)
        if entry is None:
            return ToolResult.unknown(name)
        try:
            if isinstance(entry, type):
                _LOGGER.info(f"Executing tool: {name}, args: {args}")
                content = await entry.model_validate(args).arun(tctx)
            else:
                content = await entry.execute_async(args, tctx)
            return ToolResult(content=str(content))
        except Exception as e:  # AgentInterrupted 是 CancelledError（BaseException），不会被这里吃掉
            return ToolResult.error(str(e))

    def execute_sync(self, name: str, args: dict, tctx: ToolContext | None = None) -> ToolResult:
        entry = self._entries.get(name)
        if entry is None:
            return ToolResult.unknown(name)
        try:
            if isinstance(entry, type):
                content = entry.model_validate(args).run(tctx)
            else:
                content = entry.execute_sync(args, tctx)
            return ToolResult(content=str(content))
        except Exception as e:
            return ToolResult.error(str(e))


def assemble_tool_pool(
        agent_type: Literal["main", "sub-agent", "teammate"] = "main",
        enable_experimental: bool = False,
        exclude: frozenset[str] = frozenset(),
) -> ToolPool:
    """Merge builtin tools + all MCP tools into a single ToolPool."""
    entries: dict[str, type[BaseTool] | _McpEntry] = {
        name: cls for name, cls in _TOOLS.items()
        if name not in exclude
           and agent_type in cls.model_fields["agent_type"].get_default()
           and (enable_experimental or not cls.model_fields["experimental"].get_default())
    }

    if agent_type != "teammate":
        try:
            mcp_client_manager = get_client_manager()
        except Exception as e:
            _LOGGER.exception(f"[MCP error] Init failed, falling back to builtin tools: {e}")
            mcp_client_manager = None

        if mcp_client_manager:
            sync_handlers = mcp_client_manager.tool_handlers
            async_handlers = mcp_client_manager.async_tool_handlers
            for schema in mcp_client_manager.list_tools():
                name = schema["function"]["name"]
                if name in entries or name in exclude:
                    continue
                entries[name] = _McpEntry(schema, sync_handlers.get(name), async_handlers.get(name))

    return ToolPool(entries)


if __name__ == '__main__':
    pool = assemble_tool_pool("main")
    names = [schema["function"]["name"] for schema in pool.schemas()]
    assert names and len(names) == len(set(names)) and len(names) == len(pool)
    print(names)
