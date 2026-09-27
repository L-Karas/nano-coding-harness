import sys
from typing import Any, Literal

from core.log import get_logger
from core.mcp import get_client_manager
from core.tools.base_tools import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类; noqa: F401  # import extra tools
from core.tools.extra_tools import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类; noqa: F401  # import extra tools
from core.tools.tool_base import BaseTool
from core.tools.utils import _camel_to_snake
from core.tools.web_search import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类; noqa: F401  # import extra tools

_LOGGER = get_logger(__name__)
_TOOLS: dict[str, type[BaseTool]] = {}

# 工具失败统一前缀（call_tool_handler 返回串由此生成；loop_with_interrupt 的 tool_failed 判定
# 与 TUI 渲染 error 卡均 import 本常量，避免三处手写同一字面量）
TOOL_ERROR_PREFIXES = ("[Tool Error]:", "[Unknown Tool]:")


def _register_tools() -> None:
    """Index every BaseTool subclass by snake_case tool name (read_file) — the name callers pass in."""
    for cls in BaseTool.__subclasses__():
        _TOOLS[_camel_to_snake(cls.__name__)] = cls


def _builtin_tool_classes(agent_type: Literal["main", "sub-agent", "teammate"]) -> list[type[BaseTool]]:
    """BaseTool subclasses usable by this agent level."""
    return [cls for cls in _TOOLS.values()
            if agent_type in cls.model_fields["agent_type"].get_default()]


_register_tools()


def get_builtin_tools(agent_type: Literal["main", "sub-agent", "teammate"] = "main") -> list[dict[str, Any]]:
    """
    Load tool list based on agent_type, defaults to "main" agent level.
    """
    return [cls.to_openai_tool() for cls in _builtin_tool_classes(agent_type)]


def get_builtin_tool_handlers(
        agent_type: Literal["main", "sub-agent", "teammate"] = "main",
        tool_type: Literal["sync", "async"] = "sync"
) -> dict[str, Any]:
    """
    Map each builtin tool name to its handler: the run_<tool name> function
    defined in the tool class's own module (e.g. run_read_file for ReadFile).
    """
    handlers = {}
    for cls in _builtin_tool_classes(agent_type):
        name = _camel_to_snake(cls.__name__)
        if tool_type == "sync":
            handlers[name] = getattr(sys.modules[cls.__module__], f"run_{name}")
        else:
            handlers[name] = getattr(sys.modules[cls.__module__], f"run_{name}_async")
    return handlers


def assemble_tool_pool(
        agent_type: Literal["main", "sub-agent", "teammate"] = "main",
        tool_type: Literal["sync", "async"] = "sync"
):
    """
    Merge builtin tools + all MCP tools into a single tool pool.
    """
    tools = get_builtin_tools(agent_type)
    handlers = get_builtin_tool_handlers(agent_type, tool_type)

    if agent_type != "teammate":
        try:
            mcp_client_manager = get_client_manager()
        except Exception as e:
            _LOGGER.exception(f"[MCP error] Init failed, falling back to builtin tools: {e}")
            mcp_client_manager = None

        if mcp_client_manager:
            tools.extend(mcp_client_manager.list_tools())
            handlers = handlers | (mcp_client_manager.tool_handlers
                                   if tool_type == "sync" else mcp_client_manager.async_tool_handlers)

    return tools, handlers


def call_tool_handler(handler, args: dict, name: str) -> str:
    if not handler:
        return f"{TOOL_ERROR_PREFIXES[1]} {name}"

    tool_cls = _TOOLS.get(name)
    try:
        if tool_cls:  # validate only when we know the schema; unknown name falls back to direct call
            tool_cls.model_validate(args)
        return handler(**args)
    except Exception as e:
        return f"{TOOL_ERROR_PREFIXES[0]} {e}"


async def execute_tool(handler, args: dict, name: str, ctx=None) -> str:
    if not handler:
        return f"{TOOL_ERROR_PREFIXES[1]} {name}"

    tool_cls = _TOOLS.get(name)
    try:
        if tool_cls:
            tool_cls.model_validate(args)
        _LOGGER.info(f"Executing tool: {name}, args: {args}")
        return await handler(**args, ctx=ctx)
    except Exception as e:  # AgentInterrupted 是 CancelledError（BaseException），不会被这里吃掉
        return f"{TOOL_ERROR_PREFIXES[0]} {e}"


if __name__ == '__main__':
    tools = get_builtin_tools(agent_type="main")
    handlers = get_builtin_tool_handlers(agent_type="main")
    assert set(t["function"]["name"] for t in tools) == set(handlers) and all(handlers.values())
    print(list(handlers))
