"""
s03_todo_write.py - Session Planning with TodoWrite
This chapter is about a lightweight session plan, not a durable task graph.
The model can rewrite its current plan, keep one active step in focus, and get
nudged if it stops refreshing the plan for too many rounds.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)


WORKDIR = Path.cwd()
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL")
)
MODEL = os.getenv("MODEL")

PLAN_REMINDER_INTERVAL = 3

SYSTEM = f"""You are a coding agent at {WORKDIR}.
**Note**: 
Before start work, you must use the todo tool for multi-step work.
Keep exactly one step in_progress when a task has multiple steps.
Refresh the plan as work advances. Prefer tools over prose."""


@dataclass
class PlanItem:
    content: str
    status: str = "pending"
    active_form: str = ""


@dataclass
class PlanningState:
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0


class TodoManager:
    def __init__(self):
        self.state = PlanningState()

    def update(self, items: list[PlanItem]) -> str:
        if len(items) > 12:
            raise ValueError("Keep the session plan short (max 12 items)")

        normalized = []
        in_progress_count = 0
        for index, raw_item in enumerate(items):
            content = str(raw_item.content).strip()
            status = str(raw_item.status).strip()
            active_form = str(raw_item.active_form).strip()

            if not content:
                raise ValueError(f"Item {index}: content required")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"Item {index}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1

            normalized.append(
                PlanItem(content=content, status=status, active_form=active_form)
            )

        if in_progress_count > 1:
            raise ValueError("Only one plan item can be in_progress")

        self.state.items = normalized
        self.state.rounds_since_update = 0

        return self.render()

    def render(self):
        if not self.state.items:
            return "No session plan yet."

        lines = []
        for item in self.state.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[
                item.status
            ]
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)

        completed = sum(1 for item in self.state.items if item.status == "completed")
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")

        return "\n".join(lines)

    def reminder(self):
        if not self.state.items:
            return None
        if self.state.rounds_since_update < PLAN_REMINDER_INTERVAL:
            return None
        return "<reminder>Refresh your current plan before continuing.</reminder>"

    def note_round_without_update(self):
        self.state.rounds_since_update += 1


TODO = TodoManager()


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return f"Error: Dangerous command blocked. Command: {command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

    output = (result.stdout + result.stderr).strip()
    return output[:5000] if output else "The tool no output."


def run_read(path: str, limit: Optional[int] = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]

        return "\n".join(lines)[:5000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

        return f"Work {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"

        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo": lambda **kw: TODO.update(kw["items"]),
}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file once.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Rewrite the current session plan for multi-step work.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "这一步要做什么"},
                                "status": {
                                    "type": "string",
                                    "description": "这一步现在处在什么状态",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": "这一步正在进行中时，更自然的进行时描述",
                                    "description": "Optional present-continuous label.",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]


def agent_loop(messages: list) -> None:
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
            # extra_body={"enable_thinking": False},
        )

        if response.choices[0].finish_reason == "tool_calls":
            messages.append(response.choices[0].message)
            print(f"Tool calls: {response.choices[0].message.tool_calls}")

        else:
            messages.append(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return

        results = []
        used_todo = False
        for tool_call in response.choices[0].message.tool_calls:
            handler = TOOL_HANDLERS.get(tool_call.function.name)

            try:
                args = json.loads(tool_call.function.arguments)
                output = (
                    handler(**args)
                    if handler
                    else f"Unknown tool: {tool_call.function.name}"
                )
            except Exception as e:
                output = f"Error: {e}"

            print(f"> {tool_call.function.name}: {str(output)[:100]}")
            results.append(
                {"role": "tool", "content": str(output), "tool_call_id": tool_call.id}
            )

            if tool_call.function.name == "todo":
                used_todo = True

        if used_todo:
            TODO.state.rounds_since_update = 0
        else:
            TODO.note_round_without_update()
            reminder = TODO.reminder()
            if reminder:
                results.insert(1, {"role": "user", "text": reminder})

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
