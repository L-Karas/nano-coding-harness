"""
Prompt Assemble
"""
from typing import Literal

from core.config import WORKDIR
from core.log import get_logger
from core.memory import MEMORY_MANAGER
from core.skill import list_skills
from core.template import SYSTEM_PROMPT_TEMPLATE
from core.template.prompt_template import SUB_AGENT_PROMPT_TEMPLATE

_LOGGER = get_logger(__name__)


def build_system_prompt(agent_type: Literal["main", "sub-agent", "teammate"] = "main", tools: list[dict] = []) -> str:
    _LOGGER.debug(f"Building system prompt for {agent_type}, tools: {tools}")
    tool_list = "\n".join(f"- `{tool['function']['name']}`: {tool['function']['description']}" for tool in tools)
    memory_list = "\n".join(f"- {memory.title}: {memory.content}" for memory in MEMORY_MANAGER.load_memories())
    guidelines = [
        "Be concise in your responses",
        "Show file paths clearly when working with files"
    ]

    # todo: 对不同类型智能体使用不同的提示词
    guidelines = "\n".join(f"- {guide}" for guide in guidelines)
    if agent_type == "main":
        return SYSTEM_PROMPT_TEMPLATE.format(
            tool_list=tool_list,
            memory_list=memory_list,
            skill_list=list_skills(),
            guidelines=guidelines,
            working_directory=WORKDIR
        )
    else:
        return SUB_AGENT_PROMPT_TEMPLATE.format(
            tool_list=tool_list,
            memory_list=memory_list,
            guidelines=guidelines,
            working_directory=WORKDIR
        )
