"""
Prompt Assemble
"""
import platform
import re
from functools import cache
from pathlib import Path
from typing import Literal

from core.config import WORKDIR
from core.context.memory import MEMORY_MANAGER
from core.log import get_logger
from core.skill import load_skills
from core.template import SYSTEM_PROMPT_TEMPLATE
from core.template.prompt_template import SUB_AGENT_PROMPT_TEMPLATE

_LOGGER = get_logger(__name__)

# 内容里的反斜杠会反吃掉竖线转义（"\|" 被解析成 "反斜杠 + 列分隔符"）；
# 匹配竖线前的连续反斜杠并翻倍，保证竖线始终处于被转义状态。
_PIPE_IN_CELL_RE = re.compile(r"(\\*)[|]")


def _escape_table_cell(value: object) -> str:
    """转义 Markdown 表格单元格：竖线拆列、换行断行，都会破坏表格结构。"""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _PIPE_IN_CELL_RE.sub(lambda match: match.group(1) * 2 + r"\|", text)
    return text.replace("\n", "<br>")


def _row(*cells: object) -> str:
    """转义各单元格并拼成表格行（列数与表头对齐由调用方负责）。"""
    return "|" + "|".join(_escape_table_cell(cell) for cell in cells) + "|"


def build_tools_table(tools: list[dict] | None = None) -> str:
    if not tools:
        return "(none)"
    sections = ["|Tool|Description|", "|---|---|"]
    for tool in tools:
        function = tool['function']
        sections.append(_row(f"`{function['name']}`", function['description']))

    return "\n".join(sections)


def build_memories_table() -> str:
    memories = MEMORY_MANAGER.load_memories()
    if not memories:
        return "(none)"
    sections = ["|Memory title|Memory content|Memory type|", "|---|---|---|"]
    for memory in memories:
        sections.append(_row(memory.title, memory.content, memory.mem_type))

    return "\n".join(sections)


def build_skills_table() -> str:
    skills = load_skills()
    if not skills:
        return "(none)"
    sections = ["|Skill name|Skill description|Location|", "|---|---|---|"]
    for skill in skills.values():
        sections.append(_row(f"`{skill.name}`", skill.description, f"`{skill.location}`"))

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

    # 文件工具走 Python，不认 terminal 的 shell 方言路径，提示统一使用平台原生路径格式
    sections.append("- Use the platform-native path format for `read_file` / `write_file` / `edit_file`")
    sections.append(f"- Current platform: `{platform.platform(terse=True)}`")

    return "\n".join(sections)


def build_system_prompt(
        agent_type: Literal["main", "sub-agent"] = "main",
        tools: list[dict] | None = None,
        work_directory: Path | None = None
) -> str:
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
            working_directory=work_directory or WORKDIR
        )
    else:
        return SUB_AGENT_PROMPT_TEMPLATE.format(
            tool_list=tools_table,
            memory_list=memories_table,
            guidelines=guidelines,
            working_directory=work_directory or WORKDIR
        )
