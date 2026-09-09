"""
Context
"""
from core import teammates
from core.memory import MEMORY_MANAGER


def update_context(context: dict, messages: list) -> dict:
    memories = MEMORY_MANAGER.load_memories()
    if memories:
        memories = "\n".join(f"- {mem.content}" for mem in memories)
    return {
        "memories": memories,
        "connect_mcp": [],
        "activate_teammates": list(teammates.ACTIVATE_TEAMMATES.keys())
    }
