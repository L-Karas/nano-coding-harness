"""core/context/prompt.py 表格构建与 system prompt 拼装测试"""
from pathlib import Path

import pytest

from core.context import prompt
from core.context.memory.memory import Memory
from core.skill.skills import Skill
from core.tools import shell


@pytest.fixture(autouse=True)
def _clear_guidelines_cache():
    """build_guidelines 被 @cache 记忆，monkeypatch 的 find_shell 需要每次从零构建。"""
    prompt.build_guidelines.cache_clear()


def _tool(name: str, desc: str) -> dict:
    return {"function": {"name": name, "description": desc}}


def test_build_tools_table_renders_rows():
    assert prompt.build_tools_table([_tool("read", "Read a file"), _tool("write", "Write a file")]) == (
        "|Tool|Description|\n|---|---|\n|`read`|Read a file|\n|`write`|Write a file|"
    )


def test_build_tools_table_empty():
    assert prompt.build_tools_table([]) == "(none)"
    assert prompt.build_tools_table() == "(none)"


def test_build_memories_table_renders_rows(monkeypatch):
    memories = [
        Memory(id="m1", title="语言", content="用中文回复", mem_type="user"),
        Memory(id="m2", title="环境", content="Windows", mem_type="project"),
    ]
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: memories)
    assert prompt.build_memories_table() == (
        "|Memory title|Memory content|Memory type|\n|---|---|---|\n"
        "|语言|用中文回复|user|\n|环境|Windows|project|"
    )


def test_build_memories_table_empty(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: [])
    assert prompt.build_memories_table() == "(none)"


def test_build_skills_table_renders_rows(monkeypatch):
    monkeypatch.setattr(prompt, "load_skills", lambda: {
        "review": Skill(name="review", description="Review code", content="raw", skill_type="user"),
    })
    assert prompt.build_skills_table() == (
        "|Skill name|Skill description|\n|---|---|\n|review|Review code|"
    )


def test_build_skills_table_empty(monkeypatch):
    monkeypatch.setattr(prompt, "load_skills", lambda: None)
    assert prompt.build_skills_table() == "(none)"


def test_build_guidelines_prefers_bash(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: str(Path("Git") / "bin" / "bash.exe"))
    text = prompt.build_guidelines()
    assert "bash" in text
    assert "pwsh" not in text


def test_build_guidelines_falls_back_to_powershell7(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: str(Path("PowerShell") / "7" / "pwsh.exe"))
    text = prompt.build_guidelines()
    assert "PowerShell 7 `pwsh`" in text


def test_build_guidelines_falls_back_to_windows_powershell(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: str(Path("WindowsPowerShell") / "powershell.exe"))
    text = prompt.build_guidelines()
    assert "Windows PowerShell" in text
    assert "Current platform:" in text


def test_build_guidelines_without_preferred_shell(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: None)
    text = prompt.build_guidelines()
    assert "executes commands with" not in text
    assert "Current platform:" in text


def test_build_guidelines_builds_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shell, "find_shell", lambda: calls.append(1) or None)
    prompt.build_guidelines()
    prompt.build_guidelines()
    assert len(calls) == 1


def test_build_system_prompt_main_includes_all_tables(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories",
                        lambda: [Memory(id="m1", title="t", content="c", mem_type="user")])
    monkeypatch.setattr(prompt, "load_skills",
                        lambda: {"s": Skill(name="s", description="d", content="raw",
                                            skill_type="project")})
    text = prompt.build_system_prompt("main", [_tool("read", "Read a file")])
    assert "|`read`|Read a file|" in text
    assert "|t|c|user|" in text
    assert "|s|d|" in text
    assert "(none)" not in text


def test_build_system_prompt_sub_agent_defaults_to_none_tables(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: [])
    text = prompt.build_system_prompt("sub-agent")
    assert "### Available tools:\n(none)" in text
    assert "### Memories:\n(none)" in text
    assert "{" not in text
