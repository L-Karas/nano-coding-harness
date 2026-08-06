"""
Context
"""
from src import teammates
from src.config import MEMORY_INDEX


# todo: mcp connect support
def update_context(context: dict, messages: list) -> dict:
    memories = ""
    if MEMORY_INDEX.exists():
        memories = str(MEMORY_INDEX.read_text(encoding="utf-8"))
    return {
        "memories": memories,
        "connect_mcp": [],
        "activate_teammates": list(teammates.ACTIVATE_TEAMMATES.keys())
    }
