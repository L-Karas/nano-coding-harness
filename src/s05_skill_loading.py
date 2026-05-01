"""
s05_skill_loading.py - Skills
This chapter teaches a two-layer skill model:
1. Put a cheap skill catalog in the system prompt.
2. Load the full skill body only when the model asks for it.
That keeps the prompt small while still giving the model access to reusable,
task-specific guidance.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from tool_schema import SKILLS_TOOLS

load_dotenv(override=True)

WORKDIR = Path.cwd().parent
SKILLS_DIR = WORKDIR / "skills"
print(f"Current work dir: {WORKDIR}, skills dir: {SKILLS_DIR}", file=sys.stderr)
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL")
)
MODEL = os.getenv("MODEL")


@dataclass
class SkillManifest:
    name: str
    description: str
    path: Path


@dataclass
class SkillDocument:
    manifest: SkillManifest
    body: str


class SkillRegistry:

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.documents: dict[str, SkillDocument] = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            return

        for path in sorted(self.skills_dir.rglob("SKILL.md")):
            meta, body = self._parse_frontmatter(path.read_text())
            name = meta.get("name", path.parent.name)
            description = meta.get("description", "No description")
            manifest = SkillManifest(name=name, description=description, path=path)
            self.documents[name] = SkillDocument(manifest=manifest, body=body.strip())

    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text

        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()

        return meta, match.group(2)

    def describe_available(self) -> str:
        if not self.documents:
            return "(No skills available)"

        lines = []
        for name in sorted(self.documents):
            manifest = self.documents[name].manifest
            lines.append(f"- {manifest.name}: {manifest.description}")

        return "\n\n".join(lines)

    def load_full_text(self, name: str):
        document = self.documents.get(name)
        if not document:
            know = ",".join(sorted(self.documents)) or "(none)"
            return f"Error: Unknown skill 'name'. Available skills: {know}"

        return (
            f"<skill name=\"{document.manifest.name}\">"
            f"{document.body}"
            f"</skill>"
        )


SKILL_REGISTRY = SkillRegistry(SKILLS_DIR)
SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use `load_skill` tool when a task needs specialized instructions before you act.
Skills available:
{SKILL_REGISTRY.describe_available()}
"""


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
    "load_skill": lambda **kw: SKILL_REGISTRY.load_full_text(kw["name"])
}


def agent_loop(messages: list):
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=SKILLS_TOOLS,
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
            print(f">> Tool calls: {tool_call.function.name}")
            print(f">> Tool call args: {args}")

            handler = TOOL_HANDLERS.get(tool_call.function.name)
            output = (
                handler(**args)
                if handler
                else f"Unknown tool: {tool_call.function.name}"
            )

            print(f">> Tool call result: {str(output)[:100]}")
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
            print(f"AI:\n{response_content}")

        print()
