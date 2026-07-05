"""
s10_system_prompt.py - System Prompt Construction
This chapter teaches one core idea:
the system prompt should be assembled from clear sections, not written as one
giant hardcoded blob.
Teaching pipeline:
  1. core instructions
  2. tool listing
  3. skill metadata
  4. memory section
  5. CLAUDE.md chain
  6. dynamic context
The builder keeps stable information separate from information that changes
often. A simple DYNAMIC_BOUNDARY marker makes that split visible.
Per-turn reminders are even more dynamic. They are better injected as a
separate user-role system reminder than mixed blindly into the stable prompt.
Key insight: "Prompt construction is a pipeline with boundaries, not one
big string."
"""
import re
from pathlib import Path

from config import WORKDIR

DYNAMIC_BOUNDARY = "=== DYNAMIC BOUNDARY ==="


class SystemPromptBuilder:
    """
    Assemble the system prompt from independent sections.
    The teaching goal here is clarity:
    each section has one source and one responsibility.
    That makes the prompt easier to reason about, easier to test, and easier
    to evolve as the agent grows new capabilities.
    """

    def __init__(self, workdir: Path = None, tools: list = None):
        self.workdir = workdir or WORKDIR
        self.tools = tools or []
        self.skills_dir = self.workdir / "skills"
        self.memory_dir = self.workdir / ".memory"

    def _build_core(self) -> str:
        """
        Section 1: Core instructions
        Returns:

        """
        return (
            f"You are a coding agent operating in {self.workdir}.\n"
            f"Use the provided tools to explore, read, write, and edit files.\n"
            f"Always verify before assuming. Prefer reading files over guessing."
        )

    def _build_tool_listing(self) -> str:
        """
        Section 2: Tool listings.
        Returns:

        """
        if not self.tools:
            return ""

        lines = ["# Available tools:"]
        for tool in self.tools:
            props = tool.get("function", {}).get("parameters", {}).get("properties", {})
            params = ", ".join(props.keys())
            lines.append(f"- {tool['function']['name']}({params}): {tool['description']}")
        return "\n".join(lines)

    def _build_skill_listing(self) -> str:
        """
        Section 3: Skill metadata (layer 1 from s05 concept)
        Returns:

        """
        if not self.skills_dir.exists():
            return ""

        skills = ["# Available skills\n"]
        for skill_dir in sorted(self.skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            text = skill_md.read_text()
            # parse frontmatter for name, description
            match = re.match(r"---\s*\n(.*?)\n---", text, re.DOTALL)
            if not match:
                continue

            meta = {}
            for line in match.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            name = meta.get("name", skill_dir.name)
            desc = meta.get("description", "")
            skills.append(f"- {name}: {desc}")

        if not skills:
            return ""

        return "\n".join(skills)

    def _build_memory_section(self) -> str:
        """
        Section 4: Memory content
        Returns:

        """
        if not self.memory_dir.exists():
            return ""

        memories = ["# Memories (persistent)"]
        for md_file in sorted(self.memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            text = md_file.read_text()
            match = re.match(r"---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
            if not match:
                continue

            header, body = match.group(1), match.group(2).strip()
            meta = {}
            for line in header.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            name = meta.get("name", md_file.name)
            mem_type = meta.get("type", "project")
            desc = meta.get("description", "")
            memories.append(f"[{mem_type}] {name}: {desc}\n{body}")

        if not memories:
            return ""

        return "\n\n".join(memories)

    def _build_claude_md(self) -> str:
        """
        Load CLAUDE.md file in priority order (all are included):
        1. ~/.claude/CLAUDE.md (user-global instructions)
        2. <project-root>/CLAUDE.md (project instructions)
        3. <current-subdir>/CLAUDE.md (directory-specific instructions)
        Returns:

        """
