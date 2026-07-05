"""
s11: Error Recovery - three recovery paths + exponential backoff.

Run: python s11_error_recovery/code.py

Changes from s10:
  - LLM call wrapped in try/except with three recovery paths
  - Path 1: max_tokens -> escalate 8K->64K (no append on first escalation),
            then continuation prompt (max 3)
  - Path 2: prompt_too_long -> reactive compact -> retry (once)
  - Path 3: 429/529 -> exponential backoff with jitter (max 10),
            fallback model on consecutive 529
  - with_retry wrapper for transient errors
  - RecoveryState tracks escalation / compact / 529 / model

ASCII flow:
  messages -> prompt assembly -> compress+load -> [try] LLM [except] -> tools -> loop
                                                    |          |
                                              stop_reason   error type
                                              max_tokens?   prompt_too_long? -> compact
                                              escalate /    429/529? -> backoff
                                              continue      other? -> log + exit
"""
import json
import random
from dataclasses import dataclass

import config

MEMORY_DIR = config.WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# constants
ESCALATED_MAX_TOKENS = int(64e3)
DEFAULT_MAX_TOKENS = int(8e3)
MAX_RECOVERY_RETRIES = 3
MAX_RETRIES = 10
BASE_DELAY_MS = 500
MAX_CONSECUTIVE = 3
CONTINUATION_PROMPT = """Output token limit hit. Resume directly — no apology, no recap of what
you were doing. Pick up mid-thought if that is where the cut happened.
Break remaining work into smaller pieces."""

# prompt assembly
PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: `bash`, `read_file`, `write_file`.",
    "workspace": f"Working directory: {config.WORKDIR}",
    "memory": "Relevant memories are injected below when available."
}


def assemble_system_prompt(context: dict) -> str:
    sections = [
        PROMPT_SECTIONS["identity"],
        PROMPT_SECTIONS["tools"],
        PROMPT_SECTIONS["workspace"],
    ]

    memories = context.get("memories", "")
    if memories:
        sections.append(PROMPT_SECTIONS["memory"])

    return "\n\n".join(sections)


_last_context_key, _last_prompt = None, None


def get_sys_prompt(context: dict) -> str:
    global _last_prompt, _last_context_key

    key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)
    if key == _last_context_key and _last_prompt:
        print(" \033[90m[cache hit] system prompt unchanged\033[0m")
        return _last_prompt

    _last_context_key = key
    _last_prompt = assemble_system_prompt(context)

    loaded = ["identity", "tools", "workspace"]
    if context.get("memories"):
        loaded.append("memory")

    print(f"  \033[32m[assembled] sections: {', '.join(loaded)}\033[0m")
    return _last_prompt


@dataclass
class RecoveryState:
    """
    Track recovery attempts across the loop.
    """
    has_escalated = False
    recovery_count = 0
    consecutive_529 = 0
    has_attempted_reactive_compact = False
    current_model = config.MODEL


def retry_delay(attempt, retry_after=None):
    """
    Exponential backoff with jitter. Retry-After takes priority.
    Args:
        attempt:
        retry_after:

    Returns:

    """
    if retry_after:
        return retry_after
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    jitter = random.uniform(0, base * 0.25)
    return base + jitter


def with_retry(fn, state: RecoveryState):
    """
    Exponential backoff for transient errors.
    Non-transient errors are re-raised for the outer handler.
    Args:
        fn:
        state:

    Returns:

    """
    for attempt in range(MAX_RETRIES):
        try:
            result = fn()
            state.consecutive_529 = 0
            return result
        except Exception as e:
            pass
