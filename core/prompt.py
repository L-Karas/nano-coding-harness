"""
Prompt Assemble
"""
from datetime import datetime

from core.config import WORKDIR
from core.skills import list_skills

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, edit_file, glob, "
             "todo_write, task, load_skill, compact, "
             "create_task, list_task, get_task, claim_task, complete_task, "
             "schedule_cron, list_crons, cancel_cron, "
             "spawn_teammate, send_message, check_inbox, "
             "request_shutdown, request_plan, review_plan, "
             "create_worktree, remove_worktree, keep_worktree, "
             "connect_mcp. MCP tools are prefixed mcp__{server}__{tool}.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Related memories are injected below when available."
}


def assemble_system_prompt(context: dict) -> str:
    sections = [PROMPT_SECTIONS["identity"], PROMPT_SECTIONS["tools"], PROMPT_SECTIONS["workspace"]]
    sections.append(f"Current time: {datetime.now().isoformat(timespec='seconds')}")
    sections.append("Skills catalog:\n" + list_skills() + "\nUse `load_skill` tool when a skill is relevant.")
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    # todo: mcp_client
    mcp_names = []
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")

    return "\n\n".join(sections)
