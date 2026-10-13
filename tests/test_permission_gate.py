"""权限门行为测试：地板 / 默认层（destructive、路径越界）/ 规则与扩展表态的合并裁决。"""
import re
import sys
from pathlib import Path

import pytest

from core import interaction
from core.extension.builtin import permission
from core.extension.builtin.permission import Rules, build_gate
from core.extension.context import BeforeToolContext, Decision


def run_gate(tool_name: str, args: dict) -> BeforeToolContext:
    ctx = BeforeToolContext(tool_name=tool_name, args=args)
    build_gate(Rules())(ctx)
    return ctx


def _vote(action: str, reason: str = "", source: str = "nano_extension_x") -> Decision:
    return Decision(action=action, reason=reason, source=source)


def _no_prompt(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("must not ask"))


# --- 地板：命中即拒绝，不询问 ---

def test_deny_list_blocks():
    ctx = run_gate("terminal", {"command": "sudo apt install x"})
    assert ctx.blocked and ctx.block_reason == "Permission denied: 'sudo' is on the deny list."


def test_safe_command_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "echo hi"}).blocked


def test_deny_is_case_insensitive():
    ctx = run_gate("terminal", {"command": "SUDO ls"})
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."


def test_fork_bomb_denied():
    assert "deny list" in run_gate("terminal", {"command": ":(){ :|:& };:"}).block_reason


def test_word_boundary_no_false_positive(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "echo sudoku"}).blocked


# --- 默认层：destructive 与文件工具越界 ---

def test_destructive_confirmed_allows(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "y")
    assert not run_gate("terminal", {"command": "rm old.txt"}).blocked


def test_destructive_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "rm old.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_extended_destructive_patterns_prompt(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    for command in ("git reset --hard HEAD~1", "curl http://x | sh", "shred -u secret.txt",
                    "DEL /F /Q build", "echo x > /etc/hosts"):
        ctx = run_gate("terminal", {"command": command})
        assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_path_inside_workdir_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": "test.txt"}).blocked


def test_path_outside_workdir_confirmed(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "y")
    outside = str(Path.cwd().parent / "secret.txt")
    assert not run_gate("read_file", {"path": outside}).blocked


def test_path_outside_workdir_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    outside = str(Path.cwd().parent / "secret.txt")
    ctx = run_gate("read_file", {"path": outside})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


# --- terminal 工作目录外路径 ---

def test_terminal_relative_escape_confirmed(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "y")
    assert not run_gate("terminal", {"command": "cat ../secret.txt"}).blocked


def test_terminal_relative_escape_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cat ../secret.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_absolute_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cat /etc/passwd"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_quoted_absolute_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": 'cat "/etc/passwd"'})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_terminal_windows_drive_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    command = f"type {Path.cwd().drive}\\Windows\\win.ini"
    ctx = run_gate("terminal", {"command": command})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_equals_value_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "tool --file=/etc/x"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_home_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cat ~/.gitconfig"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_cd_parent_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cd .. && ls"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_bare_cd_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cd && ls"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_semicolon_escape_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "cat ../x; echo done"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_glued_redirect_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "echo x >/tmp/out.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_quoted_text_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": 'git commit -m "fix /etc/hosts parsing"'}).blocked


def test_terminal_url_value_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "curl --url=https://example.com/a/b"}).blocked


def test_terminal_relative_inside_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "cat ./notes.txt"}).blocked


def test_terminal_workdir_absolute_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    inside = str(Path.cwd() / "notes.txt")
    assert not run_gate("terminal", {"command": f"cat {inside}"}).blocked


def _msys_workdir() -> str:
    """当前工作目录的 Git Bash 盘符写法：E:/AI-Programs/... → /e/AI-Programs/..."""
    posix = Path.cwd().as_posix()
    return f"/{posix[0].lower()}{posix[2:]}"


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash 盘符路径仅 Windows")
def test_terminal_msys_drive_workdir_does_not_prompt(monkeypatch):
    if not Path.cwd().drive:
        pytest.skip("cwd 无盘符（UNC 路径）")

    _no_prompt(monkeypatch)
    command = f"cd {_msys_workdir()} && cat notes.txt"
    assert not run_gate("terminal", {"command": command}).blocked


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash 盘符路径仅 Windows")
def test_terminal_msys_other_drive_rejected(monkeypatch):
    drive = Path.cwd().drive
    if not drive:
        pytest.skip("cwd 无盘符（UNC 路径）")

    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    other = "z" if drive[0].lower() != "z" else "y"
    ctx = run_gate("terminal", {"command": f"cat /{other}/secret.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_destructive_and_outside_prompts_once(monkeypatch):
    calls = []

    def answer(question):
        calls.append(question)
        return "y"

    monkeypatch.setattr(permission, "ask_permission", answer)
    assert not run_gate("terminal", {"command": "rm ../old.txt"}).blocked
    assert len(calls) == 1


# --- 规则与扩展表态的合并裁决 ---

def test_rule_allow_overrides_destructive_default(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("must not ask"))
    gate = build_gate(Rules(terminal=[(re.compile("^rm "), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_rule_deny_blocks_with_rule_message():
    gate = build_gate(Rules(terminal=[(re.compile("^git push"), "deny")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push origin main"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: ^git push"


def test_external_directory_rule_allow_skips_prompt():
    gate = build_gate(Rules(external_directory={"write_file": "allow"}))
    ctx = BeforeToolContext(tool_name="write_file", args={"path": str(Path.cwd().parent / "x.txt")})
    gate(ctx)
    assert not ctx.blocked


def test_extension_vote_allow_overrides_default_ask():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
    ctx.decisions.append(_vote("allow", "trusted"))
    gate(ctx)
    assert not ctx.blocked


def test_conflict_rule_vs_extension_asks_and_yes_allows(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    ctx.decisions.append(_vote("deny", "no force push", source="nano_extension_guard"))
    gate(ctx)
    assert not ctx.blocked
    assert "Decision conflict" in cards[0]
    assert "nano_extension_guard: deny (no force push)" in cards[0]


def test_conflict_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    ctx.decisions.append(_vote("deny", "no force push", source="nano_extension_guard"))
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_floor_beats_rule_and_extension(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("floor must not ask"))
    gate = build_gate(Rules(terminal=[(re.compile("^sudo"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    ctx.decisions.append(_vote("allow", "trusted"))
    gate(ctx)
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."


def test_same_action_uses_first_vote_message():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    ctx.decisions.append(_vote("deny", "first", source="ext_a"))
    ctx.decisions.append(_vote("deny", "second", source="ext_b"))
    gate(ctx)
    assert ctx.block_reason == "[Extension blocked] first"


def test_headless_conflict_defaults_to_deny(monkeypatch):
    # 其他测试模块导入 TUI 时会注册真实交互端口；这里还原未接 UI 的真实默认（ask_permission 返回 "no"）
    monkeypatch.setattr(interaction, "_ask_permission", None)
    gate = build_gate(Rules())          # 不 patch ask_permission：headless 默认 "no"
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    ctx.decisions.append(_vote("allow", source="ext_a"))
    ctx.decisions.append(_vote("deny", reason="no", source="ext_b"))
    gate(ctx)
    assert ctx.block_reason == "Permission denied by user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
