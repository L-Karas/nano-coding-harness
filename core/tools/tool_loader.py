import sys
from typing import Any, Literal

from core.tools.base_tools import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类; noqa: F401  # import extra tools
from core.tools.extra_tools import *  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类; noqa: F401  # import extra tools
from core.tools.tool_base import BaseTool
from core.tools.utils import _camel_to_snake


_TOOLS: dict[str, type[BaseTool]] = {}


def _register_tools() -> None:
    """Index every BaseTool subclass by snake_case tool name (read_file) — the name callers pass in."""
    for cls in BaseTool.__subclasses__():
        _TOOLS[_camel_to_snake(cls.__name__)] = cls


def _builtin_tool_classes(agent_level: Literal["main", "sub-agent", "teammate"]) -> list[type[BaseTool]]:
    """BaseTool subclasses usable by this agent level."""
    return [cls for cls in _TOOLS.values()
            if agent_level in cls.model_fields["agent_level"].get_default()]


_register_tools()


def get_builtin_tools(agent_level: Literal["main", "sub-agent", "teammate"] = "main") -> list[dict[str, Any]]:
    """
    Load tool list based on agent_level, defaults to "main" agent level.
    """
    return [cls.to_openai_tool() for cls in _builtin_tool_classes(agent_level)]


def get_builtin_tool_handlers(agent_level: Literal["main", "sub-agent", "teammate"] = "main") -> dict[str, Any]:
    """
    Map each builtin tool name to its handler: the run_<tool name> function
    defined in the tool class's own module (e.g. run_read_file for ReadFile).
    """
    handlers = {}
    for cls in _builtin_tool_classes(agent_level):
        name = _camel_to_snake(cls.__name__)
        handlers[name] = getattr(sys.modules[cls.__module__], f"run_{name}")
    return handlers


def call_tool_handler(handler, args: dict, name: str) -> str:
    if not handler:
        return f"Unknown: {name}"

    tool_cls = _TOOLS.get(name)
    try:
        if tool_cls:  # validate only when we know the schema; unknown name falls back to direct call
            tool_cls.model_validate(args)
        return handler(**args)
    except Exception as e:
        return f"Error: {e}"


if __name__ == '__main__':
    tools = get_builtin_tools(agent_level="main")
    handlers = get_builtin_tool_handlers(agent_level="main")
    assert set(t["function"]["name"] for t in tools) == set(handlers) and all(handlers.values())
    print(list(handlers))
