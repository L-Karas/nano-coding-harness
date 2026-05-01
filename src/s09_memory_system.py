"""
s09_memory_system.py - Memory System
This teaching version focuses on one core idea:
some information should survive the current conversation, but not everything
belongs in memory.
Use memory for:
  - user preferences
  - repeated user feedback
  - project facts that are NOT obvious from the current code
  - pointers to external resources
Do NOT use memory for:
  - code structure that can be re-read from the repo
  - temporary task state
  - secrets
Storage layout:
  .memory/
    MEMORY.md
    prefer_tabs.md
    review_style.md
    incident_board.md
Each memory is a small Markdown file with frontmatter.
The agent can save a memory through save_memory(), and the memory index
is rebuilt after each write.
An optional "Dream" pass can later consolidate, deduplicate, and prune
stored memories. It is useful, but it is not the first thing readers need
to understand.
Key insight: "Memory only stores cross-session information that is still
worth recalling later and is not easy to re-derive from the current repo."
"""
import re
from pathlib import Path
from typing import Optional

from config import WORKDIR

MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
MEMORY_TYPE = ("user", "feedback", "project", "reference")
MAX_INDEX_LINES = 200


class MemoryManager:
    """
    Load, build, and save persistent memories across sessions.
    The teaching version keeps memory explicit:
    one Markdown file per memory, plus one compact index file.
    """

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = memory_dir or MEMORY_DIR
        self.memories = {}  # name -> {description, type, content}

    def load_all(self):
        """
        Load MEMORY.md index and all individual memory files.
        Returns:

        """
        self.memories = {}
        if not self.memory_dir.exists():
            return

        # Scan all .md files except MEMORY.md
        for md_file in sorted(self.memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            parsed = self._parse_frontmatter(md_file.read_text())
            if parsed:
                name = parsed.get("name", md_file.stem)
                self.memories[name] = {
                    "description": parsed.get("description", "").strip(),
                    "type": parsed.get("type", "project").strip(),
                    "content": parsed.get("content", "").strip(),
                    "file": md_file.name
                }

        if len(self.memories):
            print(f"[Memory loaded: {len(self.memories)}] memories from {self.memory_dir}")

    def load_memory_prompt(self) -> str:
        """
        Build a memory section for injection into the system prompt.
        Returns:

        """
        if not self.memories:
            return ""

        sections = []
        sections.append("# Memories (persistent across sessions)")
        sections.append("")

        # Group by type for readability
        for mem_type in MEMORY_TYPE:
            typed = {k: v for k, v in self.memories.items() if v['type'] == mem_type}
            if not typed:
                continue
            sections.append(f"## [{mem_type}]")
            for name, mem in typed.items():
                sections.append(f"### {name}: {mem['description']}")
                if mem["content"]:
                    sections.append(mem["content"])
                sections.append("")

        return "\n".join(sections)

    def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
        """
        Save a memory to disk and update the index.
        Args:
            name:
            description:
            mem_type:
            content:

        Returns:
            returns a status message.
        """
        if mem_type not in MEMORY_TYPE:
            return f"Error: type must be one of {MEMORY_TYPE}"

        # Sanitize name for filename
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
        if not safe_name:
            return "Error: invalid memory name"

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Write individual memory file with frontmatter
        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {mem_type}\n"
            f"---\n"
            f"{content}\n"
        )

        file_name = f"{safe_name}.md"
        file_path = self.memory_dir / file_name
        try:
            file_path.write_text(frontmatter)
        except Exception as e:
            return f"Error: write memory {file_path} error, info: {e}"

        # Update in-memory store
        self.memories[name] = {
            "description": description,
            "type": mem_type,
            "content": content,
            "file": file_name
        }

        # Rebuild MEMORY.md index
        self._rebuild_index()

        return f"Saved memory '{name}' [{mem_type} to {file_path.relative_to(WORKDIR)}]"

    def _rebuild_index(self):
        """
        Rebuild MEMORY.md from current in-memory state, capped at 200 lines.
        Returns:

        """
        lines = ["# Memory Index", ""]
        for name, mem in self.memories.items():
            lines.append(f"- {name}: {mem['description']} [{mem['type']}]")
            if len(lines) >= MAX_INDEX_LINES:
                lines.append(f"... (truncated at {MAX_INDEX_LINES} lines)")
                break

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        MEMORY_INDEX.write_text("\n".join(lines) + "\n")

    def _parse_frontmatter(self, text: str) -> Optional[dict]:
        """
        Parse --- delimited frontmatter + body content.
        Args:
            text:

        Returns:

        """
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return None

        header, body = match.group(1), match.group(2)
        result = {"content": body.strip()}
        for line in header.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()

        return result


class DreamConsolidator:
    """
    Auto-consolidation of memories between sessions ("Dream").
    This is an optional later-stage feature. Its job is to prevent the memory
    store from growing into a noisy pile by merging, deduplicating, and
    pruning entries over time.
    """

    COOLDOWN_SECONDS = 86400  # 24 hours between consolidations
    SCAN_THROTTLE_SECONDS = 600  # 10 minutes between scan attempts
    MIN_SESSION_COUNT = 5  # need enough data to consolidate
    LOCK_STALE_SECONDS = 3600  # PID lock considered stale after 1 hour

    PHASES = [
        ""
    ]
