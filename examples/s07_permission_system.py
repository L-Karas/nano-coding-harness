"""
s07_permission_system.py - Permission System
Every tool call passes through a permission pipeline before execution.
Teaching pipeline:
  1. deny rules
  2. mode check
  3. allow rules
  4. ask user
This version intentionally teaches three modes first:
  - default
  - plan
  - auto
That is enough to build a real, understandable permission system without
burying readers under every advanced policy branch on day one.
Key insight: "Safety is a pipeline, not a boolean."
"""
import json
import os
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from tool_schema import CHILD_TOOLS

load_dotenv(override=True)

WORKDIR = Path.cwd().parent
client = OpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL"))
MODEL = os.getenv("MODEL")
SYSTEM = f"""You are a coding agent at {WORKDIR}. Use tools to solve tasks.
The user controls permissions. Some tool calls may be denied."""

# -- Permission modes --
# Teaching version starts with three clear modes first.
MODES = ("default", "plan", "auto")

READ_ONLY_TOOLS = {"read_file", "bash_readonly"}

# Tools that modify state
WRITE_TOOLS = {"write_file", "edit_file", "bash"}


# -- Bash security validation --
class BashSecurityValidator:
    """
    Validate bash commands for obviously dangerous patterns.
    The teaching version deliberately keeps this small and easy to read.
    First catch a few high-risk patterns, then let the permission pipeline
    decide whether to deny or ask the user.
    """

    VALIDATORS = [
        ("shell_metachar", r"[;&|`$]"),
        ("sudo", r"\bsudo\b"),
        ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),
        ("cmd_substitution", r"\$\("),
        ("ifs_injection", r"\bIFS\s*=")
    ]

    def validate(self, command: str) -> list:
        """
        Check a bash command against all validators.
        Returns list of (validator_name, matched_pattern) tuples for failures.
        An empty list means the command passed all validators.
        """
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))

        return failures

    def is_safe(self, command: str) -> bool:
        """Convenience: returns True only if no validators triggered."""
        return len(self.validate(command)) == 0

    def describe_failures(self, command: str) -> str:
        """Human-readable summary of validation failures."""
        failures = self.validate(command)
        if not failures:
            return "No issues detected"

        parts = [f"{name} (pattern: {pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)


# -- Workspace trust --
def is_workspace_trusted(workspace: Path = None) -> bool:
    """
    Check if a workspace has been explicitly marked as trusted.
    The teaching version uses a simple marker file. A more complete system
    can layer richer trust flows on top of the same idea.
    """
    ws = workspace or WORKDIR
    trust_marker = ws / ".claude" / ".claude_trusted"
    return trust_marker.exists()


# Singleton validator instance used by the permission pipeline
bash_validator = BashSecurityValidator()

# -- Permission rules --
# Rules are checked in order: first match wins.
# Format: {"tool": "<tool_name_or_*>", "path": "<glob_or_*>", "behavior": "allow|deny|ask"}
DEFAULT_RULES = [
    # Always deny dangerous patterns
    {"tool": "bash", "content": "rm -rf /", "behavior": "deny"},
    {"tool": "bash", "content": "sudo *", "behavior": "deny"},
    {"tool": "bash", "content": "mkdir *", "behavior": "deny"},
    # Allow reading anything
    {"tool": "read_file", "path": "*", "behavior": "allow"}
]


class PermissionManager:
    """
    Manages permission decisions for tool calls.
    Pipeline: deny_rules -> mode_check -> allow_rules -> ask_user
    The teaching version keeps the decision path short on purpose so readers
    can implement it themselves before adding more advanced policy layers.
    """

    def __init__(self, mode: str = "default", rules: list = None):
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}, Choose from {MODES}")

        self.mode = mode
        self.rules = rules or list(DEFAULT_RULES)
        # Simple denial tracking helps surface when the agent is repeatedly
        # asking for actions the system will not allow.
        self.consecutive_denials = 0
        self.max_consecutive_denials = 3

    def check(self, tool_name: str, tool_args: dict) -> dict:
        """
        Returns: {"behavior": "allow"|"deny"|"ask", "reason": str}
        """
        # Step 0: Bash security validation (before deny rules)
        # Teaching version checks early for clarity.
        if tool_name == "bash":
            command = tool_args.get("command", "")
            failures = bash_validator.validate(command)
            if failures:
                # Severe patterns (sudo, rm_rf) get immediate deny
                severe = {"sudo", "rm_rf"}
                severe_hits = [f for f in failures if f[0] in severe]
                if severe_hits:
                    desc = bash_validator.describe_failures(command)
                    return {
                        "behavior": "deny",
                        "reason": f"Bash validator: {desc}"
                    }

                # Other patterns escalate to ask (user can still approve)
                desc = bash_validator.describe_failures(command)
                return {
                    "behavior": "deny",
                    "reason": f"Bash validator flagged: {desc}"
                }

        # Step 1: Deny rules (bypass-immune, checked first always)
        for rule in self.rules:
            if rule["behavior"] != "deny":
                continue
            if self._matchs(rule, tool_name, tool_args):
                return {
                    "behavior": "deny",
                    "reason": f"Blocked by deny rule: {rule}"
                }

        # Step 2: Mode-based decisions
        if self.mode == "plan":
            # Plan mode: deny all write operations, allow reads
            if tool_name in WRITE_TOOLS:
                return {
                    "behavior": "deny",
                    "reason": "Plan mode: write operations are blocked"
                }
            return {
                "behavior": "allow",
                "reason": "Plan mode: read-only allowed"
            }

        if self.mode == "auto":
            # Auto mode: auto-allow read-only tools, ask for writes
            if tool_name in READ_ONLY_TOOLS or tool_name == "read_file":
                return {
                    "behavior": "allow",
                    "reason": "Auto mode: read-only tool auto-approved"
                }
            # Teaching: fall through to allow rules, then ask
            pass

        # Step 3: Allow rules
        for rule in self.rules:
            if rule["behavior"] != "allow":
                continue
            if self._matchs(rule, tool_name, tool_args):
                self.consecutive_denials = 0
                return {
                    "behavior": "allow",
                    "reason": f"Matched allow rule: {rule}"
                }

        # Step 4: Ask user (default behavior for unmatched tools)
        return {
            "behavior": "ask",
            "reason": f"No rule matched for {tool_name}, asking user"
        }

    def _matchs(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        """Check if a rule matches the tool call."""
        # Tool name match
        # 工具名匹配，当规则工具名存在、不为任意工具，且工具名匹配成功时，进行下一步工具执行路径匹配
        if rule.get("tool") and rule["tool"] != "*":
            if rule["tool"] != tool_name:
                return False

        # Path pattern match
        # 工具执行路径参数模式匹配，当路径参数与规则匹配时，执行下一步工具执行指令匹配
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False

        # Content pattern match (for bash commands)
        # 工具执行指令匹配，当工具执行指令匹配成功时，执行下一步
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False

        # 当工具名匹配，工具执行参数匹配，工具执行指令匹配均能成功时，
        # 表示该工具执行与规则匹配成功
        # 否则表示规则匹配不成功
        return True

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """Interactive approval prompt. Returns True if approved."""
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n [Permission] {tool_name}: {preview}")
        try:
            answer = input("> Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "always":
            # Add permanent allow rule for this tool
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True

        if answer in ("y", "yes"):
            self.consecutive_denials = 0
            return True

        # Track denials for circuit breaker
        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(f" [{self.consecutive_denials} consecutive denials -- "
                  f"consider switching to plan mode]")

        return False


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


def agent_loop(messages: list[dict], permission: PermissionManager):
    """
    The permission-aware agent loop.
    For each tool call:
      1. LLM requests tool use
      2. Permission pipeline checks: deny_rules -> mode -> allow_rules -> ask
      3. If allowed: execute tool, return result
      4. If denied: return rejection message to LLM
    """
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=CHILD_TOOLS,
            extra_body={"enable_thinking": False},
            max_tokens=int(8e3),
            parallel_tool_calls=True
        )

        if response.choices[0].finish_reason != "tool_calls":
            messages.append({"role": "assistant", "content": response.choices[0].message.content})
            return

        results = []
        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        for tool_call in response.choices[0].message.tool_calls:
            # -- Permission check
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            decision = permission.check(tool_name, tool_args)

            if decision["behavior"] == "deny":
                output = f"Permission denied: {decision['reason']}"
                print(f">> [DENIED] {tool_name}: {decision['reason']}")
            elif decision["behavior"] == "ask":
                if permission.ask_user(tool_name, tool_args or {}):
                    handler = TOOL_HANDLERS.get(tool_name)
                    output = handler(**(tool_args or {})) if handler else f"Unknown: {tool_name}"
                    print(f">> {tool_name}: {str(output)[:100]}")
                else:
                    output = f"Permission denied by user for {tool_name}"
            else:
                handler = TOOL_HANDLERS.get(tool_name)
                output = handler(**(tool_args or {})) if handler else f"Unknown: {tool_name}"
                print(f">> {tool_name}: {str(output)[:100]}")

            tool_call_messages[0]["tool_calls"].append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
            results.append({
                "role": "tool",
                "content": output,
                "tool_call_id": tool_call.id
            })

        messages.extend(tool_call_messages + results)


if __name__ == "__main__":
    print("Permission modes: default, plan, auto")
    mode_input = input("Mode (default): ").strip().lower() or "default"
    if mode_input not in MODES:
        mode_input = "default"

    permission = PermissionManager(mode_input)
    print(f"[Permission mode: {mode_input}]")

    messages = [{"role": "system", "content": SYSTEM}]

    while True:
        try:
            query = input("\033[36ms03 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        # /mode command to switch modes at runtime
        if query.startswith("/mode"):
            parts = query.split()
            if len(parts) == 2 and parts[1] in MODES:
                permission.mode = parts[1]
                print(f"[Switched to {parts[1]} mode]")
            else:
                print(f"Usage: /mode <{'|'.join(MODES)}")
            continue

        # /rules command to show current rules
        if query.strip() == "/rules":
            for i, rule in enumerate(permission.rules):
                print(f"  {i}: {rule}")
            continue

        messages.append({"role": "user", "content": query})
        agent_loop(messages, permission)

        response_content = messages[-1]["content"]
        if response_content:
            print(f"AI:\n{response_content}")

        print()
