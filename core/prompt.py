"""
Prompt Assemble
"""
from typing import Literal

from core.config import WORKDIR
from core.context.memory import MEMORY_MANAGER
from core.log import get_logger
from core.skill import load_skills
from core.template import SYSTEM_PROMPT_TEMPLATE
from core.template.prompt_template import SUB_AGENT_PROMPT_TEMPLATE

_LOGGER = get_logger(__name__)


# todo: 缓存，避免每次重建
def build_tools_table(tools: list[dict] | None = None) -> str:
    if not tools:
        return "(none)"
    sections = ["|Tool|Description|", "|---|---|"]
    for tool in tools:
        sections.append(f"|`{tool['function']['name']}`|{tool['function']['description']}|")

    return "\n".join(sections)


def build_memories_table() -> str:
    memories = MEMORY_MANAGER.load_memories()
    if not memories:
        return "(none)"
    sections = ["|Memory title|Memory content|Memory type|", "|---|---|---|"]
    for memory in memories:
        sections.append(f"|{memory.title}|{memory.content}|{memory.mem_type}|")

    return "\n".join(sections)


def build_skills_table() -> str:
    skills = load_skills()
    if not skills:
        return "(none)"
    sections = ["|Skill name|Skill description|", "|---|---|"]
    for _, skill in skills.items():
        sections.append(f"|{skill['name']}|{skill['description']}|")

    return "\n".join(sections)


def build_system_prompt(agent_type: Literal["main", "sub-agent", "teammate"] = "main",
                        tools: list[dict] | None = None) -> str:
    tools = tools or []
    _LOGGER.debug(f"Building system prompt for {agent_type}, {len(tools)} tools")
    tools_table = build_tools_table(tools)
    memories_table = build_memories_table()
    skills_table = build_skills_table()
    guidelines = "\n".join(f"- {guide}" for guide in [
        "Be concise in your responses",
        "Show file paths clearly when working with files"
    ])

    if agent_type == "main":
        return SYSTEM_PROMPT_TEMPLATE.format(
            tool_list=tools_table,
            memory_list=memories_table,
            skill_list=skills_table,
            guidelines=guidelines,
            working_directory=WORKDIR
        )
    else:
        return SUB_AGENT_PROMPT_TEMPLATE.format(
            tool_list=tools_table,
            memory_list=memories_table,
            guidelines=guidelines,
            working_directory=WORKDIR
        )
