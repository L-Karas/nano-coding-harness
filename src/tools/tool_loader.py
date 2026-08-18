from typing import Any, Literal

from tools import base_tools  # noqa: F401  # 导入内置工具类以注册 BaseTool 子类
from tools import extra_tools  # noqa: F401  # import extra tools
from tools.tool_base import BaseTool


def get_builtin_tools(agent_level: Literal["sub", "main"] = "main") -> list[dict[str, Any]]:
    """
    Load tool list based on agent_level, defaults to "main" agent level.
    """

    # todo: add main agent tools
    if agent_level == "main":
        tools = [tool_cls.to_openai_tool() for tool_cls in BaseTool.__subclasses__()]
    else:
        tools = [tool_cls.to_openai_tool() for tool_cls in BaseTool.__subclasses__() if
                 tool_cls.model_fields["agent_level"].default == agent_level]

    return tools
