"""
s04_subagent.py - Subagents
Spawn a child agent with fresh messages=[]. The child works in its own
context, sharing the filesystem, then returns only a summary to the parent.
    Parent agent                     Subagent
    +------------------+             +------------------+
    | messages=[...]   |             | messages=[]      |  <-- fresh
    |                  |  dispatch   |                  |
    | tool: task       | ---------->| while tool_use:  |
    |   prompt="..."   |            |   call tools     |
    |   description="" |            |   append results |
    |                  |  summary   |                  |
    |   result = "..." | <--------- | return last text |
    +------------------+             +------------------+
              |
    Parent context stays clean.
    Subagent context is discarded.
Key insight: "Fresh messages=[] gives context isolation. The parent stays clean."
Note: Real Claude Code also uses in-process isolation (not OS-level process
forking). The child runs in the same process with a fresh message array and
isolated tool context -- same pattern as this teaching implementation.
    Comparison with real Claude Code:
    +-------------------+------------------+----------------------------------+
    | Aspect            | This demo        | Real Claude Code                 |
    +-------------------+------------------+----------------------------------+
    | Backend           | in-process only  | 5 backends: in-process, tmux,    |
    |                   |                  | iTerm2, fork, remote             |
    | Context isolation | fresh messages=[]| createSubagentContext() isolates  |
    |                   |                  | ~20 fields (tools, permissions,  |
    |                   |                  | cwd, env, hooks, etc.)           |
    | Tool filtering    | manually curated | resolveAgentTools() filters from |
    |                   |                  | parent pool; allowedTools         |
    |                   |                  | replaces all allow rules         |
    | Agent definition  | hardcoded system | .claude/agents/*.md with YAML    |
    |                   | prompt           | frontmatter (AgentTemplate)      |
    +-------------------+------------------+----------------------------------+
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from tool_schema import CHILD_TOOLS, PARENT_TOOLS

load_dotenv(override=True)

WORKDIR = Path.cwd()
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL")
)
MODEL = os.getenv("MODEL")

SYSTEM = f"You are a coding agent at {WORKDIR}. Use the `task` tool to delegate exploration or subtasks."
SUBAGENT_SYSTEM = f"You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings."


class AgentTemplate:
    """
    Parse agent definition from markdown frontmatter.
    Real Claude Code loads agent definitions from .claude/agents/*.md.
    Frontmatter fields: name, tools, disallowedTools, skills, hooks,
    model, effort, permissionMode, maxTurns, memory, isolation, color,
    background, initialPrompt, mcpServers.
    3 sources: built-in, custom (.claude/agents/), plugin-provided.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.stem
        self.config = {}
        self.system_prompt = ""
        self._parse()

    def _parse(self):
        text = self.path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            self.system_prompt = text
            return

        for line in match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                self.config[k.strip()] = v.strip()

        self.system_prompt = match.group(2).strip()
        self.name = self.config.get("name", self.name)


# -- Tool implementations shared by parent and child --
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return f"Error: Dangerous command blocked, command: {command}"

    try:
        output = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (output.stdout + output.stderr).strip()
        return output[:5000] if output else "Tool no output"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}


# -- Subagent: fresh context, filtered tools, summary-only return --
def run_subagent(prompt: str) -> str:
    sub_messages = [
        {"role": "system", "content": SUBAGENT_SYSTEM},
        {"role": "user", "comtent": prompt},
    ]

    # safety limit
    for _ in range(30):
        response = client.chat.completions.create(
            model=MODEL,
            messages=sub_messages,
            tools=CHILD_TOOLS,
            max_tokens=8000,
            extra_body={"enable_thinking": False},
        )

        if response.choices[0].finish_reason != "tool_calls":
            sub_messages.append({"role": "assistant", "content": response.choices[0].message.content})
            break
        else:
            sub_messages.append(response.choices[0].message)

        results = []
        for tool_call in response.choices[0].message.tool_calls:
            handler = TOOL_HANDLERS.get(tool_call.function.name)

            try:
                args = json.loads(tool_call.function.arguments)
                print(f">>>> Subagent tool calls: {tool_call.function.name}", file=sys.stderr)
                print(f">>>> Subagent tool call args: {args}", file=sys.stderr)
                output = (
                    handler(**args)
                    if handler
                    else f"Unknown tool: {tool_call.function.name}"
                )
            except Exception as e:
                output = f"Error: {e}"

            results.append(
                {"role": "tool", "content": str(output), "tool_call_id": tool_call.id}
            )

        sub_messages.extend(results)

    # Only the final text returns to the parent -- child context is discarded
    return (
        sub_messages[-1]["content"]
        if sub_messages[-1]["content"]
        else "task no summary"
    )


def agent_loop(messages: list):
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=PARENT_TOOLS,
            max_tokens=8000,
            extra_body={"enable_thinking": False},
        )

        if response.choices[0].finish_reason == "tool_calls":
            messages.append(response.choices[0].message)
        else:
            messages.append(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return

        results = []
        for tool_call in response.choices[0].message.tool_calls:
            args = json.loads(tool_call.function.arguments)
            print(f"Tool calls: {tool_call.function.name}", file=sys.stderr)
            print(f"Tool call args: {args}", file=sys.stderr)
            if tool_call.function.name == "task":
                description = args.get("description", "subtask")
                prompt = args.get("prompt", "")
                print(f"> task ({description}): {prompt[:80]}")

                output = run_subagent(prompt)
            else:
                handler = TOOL_HANDLERS.get(tool_call.function.name)
                output = (
                    handler(**args)
                    if handler
                    else f"Unknown tool: {tool_call.function.name}"
                )

            print(f"  {str(output)[:100]}")
            results.append(
                {"role": "tool", "content": str(output), "tool_call_id": tool_call.id}
            )

        messages.extend(results)


if __name__ == "__main__":
    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        messages.append({"role": "user", "content": query})
        agent_loop(messages)

        response_content = messages[-1]["content"]
        if response_content:
            print(f"AI:\n{response_content}", file=sys.stderr)

        print()
