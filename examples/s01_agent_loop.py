"""
s01_agent_loop.py - The Agent Loop
This file teaches the smallest useful coding-agent pattern:
    user message
      -> model reply
      -> if tool_use: execute tools
      -> write tool_result back to messages
      -> continue
It intentionally keeps the loop small, but still makes the loop state explicit
so later chapters can grow from the same structure.
"""

import json
import os
import subprocess
from dataclasses import dataclass

import dotenv
from openai import OpenAI

# try:
#     import readline
#     readline.parse_and_bind("set bind-tty-special-chars off")
#     readline.parse_and_bind("set input-meta on")
#     readline.parse_and_bind("set output-meta on")
#     readline.parse_and_bind("set convert-meta off")
#     readline.parse_and_bind("set enable-meta-keybindings on")
# except Exception:
#     pass

dotenv.load_dotenv(override=True)

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
)

model = os.getenv("MAIN_MODEL")

SYSTEM = """You are a coding agent at {os.getcwd()}.
Use bash to inspect and change the workspace. Act first, then report clearly."""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the current workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string"
                }
            },
            "required": ["command"]
        }
    }
}]


@dataclass
class LoopState:
    messages: list
    turn_count: int = 1
    transition_reason: str | None = None


def run_bash(command: str) -> str:
    dangerous_command = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous_command):
        return "Error: Dangerous command blocker."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except Exception as e:
        return f"Tool Error: {e}"

    output = (result.stdout + result.stderr).strip()
    return output[:int(5e4)] if output else "Tool execute success."


def extract_text(content: list) -> str:
    if not isinstance(content, list):
        return ""

    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)

    return "\n".join(texts).strip()


def execute_tool_calls(response_content: list) -> list[dict]:
    results = []

    for block in response_content:
        if block["type"] != "function":
            continue

        command = json.loads(block["function"]["arguments"])["command"]
        print(f"\033[33m$ {command}\033[0m")
        output = run_bash(command)
        print("Tool:\n", output[:200])
        results.append({
            "role": "tool",
            "tool_call_id": block["id"],
            "content": output,
        })

    return results


def run_one_turn(state: LoopState) -> bool:
    response = client.chat.completions.create(
        model=model,
        messages=state.messages,
        extra_body={"enable_thinking": False},
        tools=TOOLS,
        max_tokens=8000,
    )
    response_message = response.choices[0].message.model_dump()

    state.messages.append(response.choices[0].message)

    if not response_message["tool_calls"]:
        state.transition_reason = None
        return False

    tool_results = execute_tool_calls(response_message["tool_calls"])

    if not tool_results:
        state.transition_reason = None
        return False

    state.messages.extend(tool_results)
    state.turn_count += 1
    state.transition_reason = "tool_result"

    return True


def agent_loop(state: LoopState) -> None:
    while run_one_turn(state):
        pass


if __name__ == "__main__":
    history = []

    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except Exception:
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        state = LoopState(messages=history)
        agent_loop(state)

        final_text = history[-1]["content"]
        if final_text:
            print("AI:\n", final_text)

        print()
