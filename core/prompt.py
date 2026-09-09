"""
Prompt Assemble
"""
from typing import Literal

from core.config import WORKDIR
from core.memory import MEMORY_MANAGER
from core.skill import list_skills
from core.template import SYSTEM_PROMPT_TEMPLATE
from core.template.prompt_template import SUB_AGENT_PROMPT_TEMPLATE


# PROMPT_SECTIONS = {
#     "identity": "You are a coding agent. Act, don't explain.",
#     "tools": "Available tools: bash, read_file, write_file, edit_file, glob, "
#              "todo_write, task, load_skill, compact, "
#              "create_task, list_task, get_task, claim_task, complete_task, "
#              "schedule_cron, list_crons, cancel_cron, "
#              "spawn_teammate, send_message, check_inbox, "
#              "request_shutdown, request_plan, review_plan, "
#              "create_worktree, remove_worktree, keep_worktree. "
#              "MCP tools are prefixed mcp__{server}__{tool}.",
#     "workspace": f"Working directory: {WORKDIR}",
#     "memory": "Related memories are injected below when available."
# }
#
#
# def assemble_system_prompt(context: dict) -> str:
#     sections = [PROMPT_SECTIONS["identity"], PROMPT_SECTIONS["tools"], PROMPT_SECTIONS["workspace"]]
#     sections.append(f"Current time: {datetime.now().isoformat(timespec='seconds')}")
#     sections.append("Skills catalog:\n" + list_skills() + "\nUse `load_skill` tool when a skill is relevant.")
#     if context.get("memories"):
#         sections.append(f"Relevant memories:\n{context['memories']}")
#     # todo: mcp_client
#     mcp_names = []
#     if mcp_names:
#         sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")
#
#     return "\n\n".join(sections)


def build_system_prompt(agent_type: Literal["main", "sub-agent", "teammate"] = "main", tools: list[dict] = []) -> str:
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
            skill_list=list_skills,
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