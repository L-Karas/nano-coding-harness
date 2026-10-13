"""core/context/prompt.py 表格构建与 system prompt 拼装测试"""
from itertools import groupby
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


def _cell_count(line: str) -> int:
    """按未转义竖线切分统计列数；独立于实现验证表格结构。"""
    count, escaped = 1, False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            count += 1
    return count


def _table_widths(text: str) -> list[set[int]]:
    """按连续表格行分组，返回每组内各行的列数集合（组内应恰好一个值）。"""
    groups = groupby(text.splitlines(), key=lambda line: line.startswith("|"))
    return [{_cell_count(line) for line in lines} for is_table, lines in groups if is_table]


def test_build_tools_table_renders_rows():
    assert prompt.build_tools_table([_tool("read", "Read a file"), _tool("write", "Write a file")]) == (
        "|Tool|Description|\n|---|---|\n|`read`|Read a file|\n|`write`|Write a file|"
    )


def test_build_tools_table_empty():
    assert prompt.build_tools_table([]) == "(none)"
    assert prompt.build_tools_table() == "(none)"


def test_build_memories_table_renders_rows(monkeypatch):
    memories = [
        Memory(
            id="m1",
            title="语言",
            content="用中文回复",
            mem_type="user",
        ),
        Memory(
            id="m2",
            title="环境",
            content="Windows",
            mem_type="project",
        ),
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
        "review": Skill(
            name="review",
            description="Review code",
            content="raw",
            location="/skills/review",
            skill_type="user",
        ),
    })
    assert prompt.build_skills_table() == (
        "|Skill name|Skill description|Location|\n|---|---|---|\n"
        "|`review`|Review code|`/skills/review`|"
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


def test_build_guidelines_file_tools_use_native_paths(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: None)
    text = prompt.build_guidelines()
    line = next(line for line in text.splitlines() if "read_file" in line)
    for name in ("read_file", "write_file", "edit_file"):
        assert f"`{name}`" in line
    assert "platform-native" in line


def test_build_guidelines_builds_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shell, "find_shell", lambda: calls.append(1) or None)
    prompt.build_guidelines()
    prompt.build_guidelines()
    assert len(calls) == 1


def test_build_system_prompt_main_includes_all_tables(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories",
                        lambda: [Memory(
                            id="m1",
                            title="t",
                            content="c",
                            mem_type="user",
                        )])
    monkeypatch.setattr(prompt, "load_skills",
                        lambda: {"s": Skill(
                            name="s",
                            description="d",
                            content="raw",
                            location="/skills/s",
                            skill_type="project",
                        )})
    text = prompt.build_system_prompt("main", [_tool("read", "Read a file")])
    assert "|`read`|Read a file|" in text
    assert "|t|c|user|" in text
    assert "|`s`|d|`/skills/s`|" in text
    assert "(none)" not in text


def test_build_system_prompt_sub_agent_defaults_to_none_tables(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: [])
    text = prompt.build_system_prompt("sub-agent")
    assert "### Available tools:\n(none)" in text
    assert "### Memories:\n(none)" in text
    assert "{" not in text


def test_build_tools_table_escapes_pipes_and_newlines():
    table = prompt.build_tools_table([_tool("read", "Read|write\nsecond line")])
    assert table == "|Tool|Description|\n|---|---|\n|`read`|Read\\|write<br>second line|"
    assert _table_widths(table) == [{4}]


def test_build_memories_table_escapes_pipes_and_newlines(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: [
        Memory(
            id="m1",
            title="a|b",
            content="line1\r\nline2",
            mem_type="user",
        ),
    ])
    table = prompt.build_memories_table()
    assert table == (
        "|Memory title|Memory content|Memory type|\n|---|---|---|\n"
        "|a\\|b|line1<br>line2|user|"
    )
    assert _table_widths(table) == [{5}]


def test_build_skills_table_escapes_pipes_and_newlines(monkeypatch):
    monkeypatch.setattr(prompt, "load_skills", lambda: {
        "review": Skill(
            name="review|v2",
            description="Review\rcode",
            content="raw",
            location="/skills/review",
            skill_type="user",
        ),
    })
    table = prompt.build_skills_table()
    assert table == (
        "|Skill name|Skill description|Location|\n|---|---|---|\n"
        "|`review\\|v2`|Review<br>code|`/skills/review`|"
    )
    assert _table_widths(table) == [{5}]


def test_pipe_preceded_by_backslash_keeps_cell_boundary():
    # 内容自带 "\|"：若只补一个反斜杠，反斜杠会成对耗尽，竖线重新变成列分隔符
    table = prompt.build_tools_table([_tool("grep", r"pattern a\|b")])
    assert table == "|Tool|Description|\n|---|---|\n" + r"|`grep`|pattern a\\\|b|"
    assert _table_widths(table) == [{4}]


def test_build_system_prompt_tables_keep_uniform_width(monkeypatch):
    monkeypatch.setattr(prompt.MEMORY_MANAGER, "load_memories", lambda: [
        Memory(
            id="m1",
            title="t|1",
            content="c\n2",
            mem_type="user",
        ),
    ])
    monkeypatch.setattr(prompt, "load_skills", lambda: {
        "s": Skill(
            name="s|1",
            description="d\n2",
            content="raw",
            location="/skills/s",
            skill_type="project",
        ),
    })
    text = prompt.build_system_prompt("main", [_tool("read", "r|1\n2")])

    # 工具 2 列、记忆 3 列、技能 3 列；每张表内各行宽度须一致
    assert _table_widths(text) == [{4}, {5}, {5}]
