"""
Prompt Assemble
"""
import platform
from functools import cache
from typing import Literal

from core.config import WORKDIR
from core.context.memory import MEMORY_MANAGER
from core.log import get_logger
from core.skill import load_skills
from core.template import SYSTEM_PROMPT_TEMPLATE
from core.template.prompt_template import SUB_AGENT_PROMPT_TEMPLATE

_LOGGER = get_logger(__name__)


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


@cache
def build_guidelines() -> str:
    """shell / 平台指南：运行期不变量，进程内只构建一次。"""
    # 函数级导入打破循环：core.tools 包初始化会经 subagent → core.sub_agent 回头导入本模块
    from core.tools.shell import find_shell, shell_kind

    sections = ["- Be concise in your responses", "- Show file paths clearly when working with files"]

    # shell_kind 为 None 时 terminal 走系统默认 shell（cmd/sh），不声称具体方言
    kind = shell_kind(find_shell())
    if kind == "bash":
        sections.append("- The `terminal` tool executes commands with bash: use bash syntax")
    elif kind == "pwsh":
        sections.append("- The `terminal` tool executes commands with PowerShell 7 `pwsh`: use PowerShell syntax")
    elif kind == "powershell":
        sections.append("- The `terminal` tool executes commands with Windows PowerShell: use PowerShell syntax")

    sections.append(f"- Current platform: `{platform.platform(terse=True)}`")

    return "\n".join(sections)


def build_system_prompt(agent_type: Literal["main", "sub-agent", "teammate"] = "main",
                        tools: list[dict] | None = None) -> str:
    tools = tools or []
    _LOGGER.debug(f"Building system prompt for {agent_type}, {len(tools)} tools")
    tools_table = build_tools_table(tools)
    memories_table = build_memories_table()
    skills_table = build_skills_table()
    guidelines = build_guidelines()

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
