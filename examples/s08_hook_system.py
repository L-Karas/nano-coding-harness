"""
s08_hook_system.py - Hook System
Hooks are extension points around the main loop.
They let readers add behavior without rewriting the loop itself.
Teaching version:
  - SessionStart
  - PreToolUse
  - PostToolUse
Teaching exit-code contract:
  - 0 -> continue
  - 1 -> block
  - 2 -> inject a message
This is intentionally simpler than a production system. The goal here is to
teach the extension pattern clearly before introducing event-specific edge
cases.
Key insight: "Extend the agent without touching the loop."
"""
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from tool_schema import CHILD_TOOLS
from tools import BASE_TOOL_HANDLERS

load_dotenv(override=True)

WORKDIR = Path.cwd().parent
client = OpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL"))
MODEL = os.getenv("MODEL")

# The teaching version keeps only the three clearest events. More complete
# systems can grow the event surface later.
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart")
HOOK_TIMEOUT = 30  # seconds
# Real CC timeouts:
#   TOOL_HOOK_EXECUTION_TIMEOUT_MS = 600000 (10 minutes for tool hooks)
#   SESSION_END_HOOK_TIMEOUT_MS = 1500 (1.5 seconds for SessionEnd hooks)

# Workspace trust marker. Hooks only run if this file exists (or SDK mode).
TRUST_MARKER = WORKDIR / ".claude" / ".claude_trusted"


class HookManager:
    """
    Load and execute hooks from .hooks.json configuration.
    The hook manager does three simple jobs:
    - load hook definitions
    - run matching commands for an event
    - aggregate block / message results for the caller
    """

    def __init__(self, config_path: Path = None, sdk_mode: bool = False):
        self.hooks = {"PreToolUse": [], "PoseToolUse": [], "SessionStart": []}
        self._sdk_mode = sdk_mode
        config_path = config_path or (WORKDIR / ".hooks.json")

        if config_path.exists():
            try:
                config: dict = json.loads(config_path.read_text())
                for event in HOOK_EVENTS:
                    self.hooks[event] = config.get("hooks", {}).get(event, [])
                print(f"[Hooks loaded from {config_path}]")
            except Exception as e:
                print(f"[Hook config error: {e}]")

    def _check_workspace_trust(self) -> bool:
        """
        Check whether the current workspace is trusted.
        The teaching version uses a simple trust marker file.
        In SDK mode, trust is treated as implicit.
        """
        if self._sdk_mode:
            return True
        return TRUST_MARKER.exists()

    def run_hooks(self, event: str, context: dict = None) -> dict:
        """
        Execute all hooks for an event.
        Returns: {"blocked": bool, "messages": list[str]}
          - blocked: True if any hook returned exit code 1
          - messages: stderr content from exit-code-2 hooks (to inject)
        """
        result = {"blocked": False, "messages": []}

        # Trust gate: refuse to run hooks in untrusted workspaces
        if not self._check_workspace_trust():
            return result

        hooks = self.hooks.get(event, [])

        for hook_def in hooks:
            # Check matcher (tool name filter for PreToolUse/PostToolUse)
            matcher = hook_def.get("matcher")
            if matcher and context:
                tool_name = context.get("tool_name", "")
                if matcher != "*" and matcher != tool_name:
                    continue

            command = hook_def.get("command", "")
            if not command:
                continue

            # Build environment with hook context
            env = dict(os.environ)
            if context:
                env["HOOK_EVENT"] = event
                env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
                env["HOOK_TOOL_INPUT"] = json.dumps(context.get("tool_input", {}), ensure_ascii=False)[:10000]

                if "tool_output" in context:
                    env["HOOK_TOOL_OUTPUT"] = str(context["tool_output"])[:10000]

            try:
                output = subprocess.run(
                    command, shell=True, cwd=WORKDIR, env=env,
                    capture_output=True, text=True, timeout=HOOK_TIMEOUT
                )

                if output.returncode == 0:
                    # Continue silently
                    if output.stdout.strip():
                        print(f"  [hook: {event}] {output.stdout.strip()[:100]}")

                    # Optional structured stdout: small extension point that
                    # keeps the teaching contract simple.
                    try:
                        hook_output = json.loads(output.stdout)
                        if "updatedInput" in hook_output and context:
                            context["tool_input"] = hook_output["updatedInput"]
                        if "additionalContext" in hook_output:
                            result["messages"].append(hook_output["additionalContext"])
                        if "permissionDecision" in hook_output:
                            result["permission_override"] = (hook_output["permissionDecision"])
                    except (json.JSONDecodeError, TypeError):
                        # stdout was not JSON -- normal for simple hooks
                        pass

                elif output.returncode == 1:
                    # Block execution
                    result["blocked"] = True
                    reason = output.stderr.strip() or "Blocked by hook"
                    result["block_reason"] = reason
                    print(f"  [hook: {event}] BLOCKED: {reason[:100]}")


                elif output.returncode == 2:
                    # Inject message
                    msg = output.stderr.strip()
                    if msg:
                        result["messages"].append(msg)
                        print(f"  [hook:{event}] INJECT: {msg[:100]}")

            except subprocess.TimeoutExpired:
                print(f"  [hook:{event}] Timeout: ({HOOK_TIMEOUT}s)")
            except Exception as e:
                print(f"  [hook:{event}] Error: {e}")

        return result


def agent_loop(messages: list, hooks: HookManager):
    """
    The hook-aware agent loop.
    The teaching version keeps only the clearest integration points:
    SessionStart, PreToolUse, execute tool, PostToolUse.
    """
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=CHILD_TOOLS,
            max_tokens=int(8e3),
            extra_body={"enable_thinking": False}
        )

        if response.choices[0].finish_reason != "tool_calls":
            messages.append({"role": "assistant", "content": response.choices[0].message.content})
            return

        results = []
        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        for tool_call in response.choices[0].message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            ctx = {"tool_name": tool_name, "tool_input": tool_args}

            # -- PreToolUse hooks --
            pre_result = hooks.run_hooks("PreToolUse", ctx)

            # Inject hook messages into results
            for msg in pre_result.get("messages", []):
                results.append({
                    "role": "tool",
                    "content": f"[Hook message]: {msg}",
                    "tool_call_id": tool_call.id
                })

            if pre_result.get("blocked"):
                reason = pre_result.get("block_reason", "Blocked by hook")
                results.append({
                    "role": "tool",
                    "content": f"Tool blocked by PreToolUse hook: {reason}",
                    "tool_call_id": tool_call.id
                })
                continue

            # -- execute tool --
            handler = BASE_TOOL_HANDLERS.get(tool_name)
            try:
                output = handler(**tool_args) if handler else f"Unknown: {tool_name}"
            except Exception as e:
                output = f"Error: {e}"
            print(f"> {tool_name}: {str(output)[:100]}")

            # -- PostToolUse hooks --
            ctx["tool_output"] = output
            post_result = hooks.run_hooks("PostToolUse", ctx)

            # Inject post-hook messages
            for msg in post_result.get("messages", []):
                output += f"\n[Hook note]: {msg}"

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
                "content": str(output),
                "tool_call_id": tool_call.id
            })

        messages.extend(tool_call_messages + results)


if __name__ == "__main__":
    hooks = HookManager(sdk_mode=True)

    SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks."

    # First SessionStart hooks
    hooks.run_hooks("SessionStart", {"tool_name": "", "tool_input": {}})

    messages = [{"role": "system", "content": SYSTEM}]

    while True:
        try:
            query = input("\033[36ms03 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        messages.append({"role": "user", "content": query})
        agent_loop(messages, hooks)

        response_content = messages[-1]["content"]
        if response_content:
            print(f"AI:\n{response_content}")

        print()
