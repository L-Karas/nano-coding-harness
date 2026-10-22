"""权限门行为测试：地板 / 默认层（destructive、路径越界）/ 规则与扩展表态的合并裁决。"""
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import interaction
from core.config import AgentConfig
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: pytest.fail("must not ask"))


def _with_mode(monkeypatch, mode: str) -> None:
    """固定审批模式：权限门每次裁决读全局单例，测试不能依赖本机 .settings.json。"""
    monkeypatch.setattr(permission, "CONFIGMANAGER",
                        SimpleNamespace(config=AgentConfig(approval_mode=mode)))


@pytest.fixture(autouse=True)
def _manual_mode(monkeypatch):
    """既有默认层用例默认 Manual；需要 Yolo 的用例在测试内再调 _with_mode 覆盖。"""
    _with_mode(monkeypatch, "Manual")


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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo format c:"}).blocked
    assert all("deny list" not in card for card in cards)


def test_redirect_to_dev_null_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo hi > /dev/null"}).blocked
    assert all("deny list" not in card for card in cards)


def test_quoted_function_definition_not_denied(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": 'echo "a(){ a|a& }"'}).blocked


def test_of_dev_path_in_argument_not_floor_denied(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "dd --version; echo of=/dev/sda"}).blocked
    assert all("deny list" not in card for card in cards)


# --- 默认层：destructive 与文件工具越界 ---

def test_destructive_confirmed_allows(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "y")
    assert not run_gate("terminal", {"command": "rm old.txt"}).blocked


def test_destructive_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "rm old.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_destructive_prompt_carries_command_detail(monkeypatch):
    """确认卡的触发摘要与工具卡参数行同构：command: <命令原文>。"""
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    run_gate("terminal", {"command": "rm old.txt"})
    assert cards == [("Destructive command detected", ["command: rm old.txt"])]


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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
    assert not run_gate("terminal", {"command": "echo x >/etc/hosts"}).blocked
    assert "Destructive command detected" in cards[0]


def test_path_inside_workdir_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": "test.txt"}).blocked


def test_path_outside_workdir_confirmed(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "y")
    outside = str(Path.cwd().parent / "secret.txt")
    assert not run_gate("read_file", {"path": outside}).blocked


def test_path_outside_workdir_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    outside = str(Path.cwd().parent / "secret.txt")
    ctx = run_gate("read_file", {"path": outside})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_file_path_prompt_carries_path_detail(monkeypatch):
    """确认卡的触发摘要与工具卡参数行同构：path: <路径>。"""
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    outside = str(Path.cwd().parent / "secret.txt")
    run_gate("read_file", {"path": outside})
    assert cards == [("Access outside working directory:", [f"path: {outside}"])]


# --- terminal 工作目录外路径 ---

def test_terminal_relative_escape_confirmed(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "y")
    assert not run_gate("terminal", {"command": "cat ../secret.txt"}).blocked


def test_terminal_relative_escape_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cat ../secret.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_outside_paths_listing_moves_to_detail(monkeypatch):
    """越界路径清单属触发数据：移入 details（工具卡正文样式），确认文本只留标题。"""
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    run_gate("terminal", {"command": "cat ../secret.txt"})
    assert cards == [("Access outside working directory:",
                      ["- ../secret.txt", "command: cat ../secret.txt"])]


def test_outside_paths_listing_truncates_in_detail(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    command = "cat ../a ../b ../c ../d ../e ../f"
    run_gate("terminal", {"command": command})
    question, details = cards[0]
    assert question == "Access outside working directory:"
    assert details == ["- ../a\n- ../b\n- ../c\n- ... and 3 more",
                       f"command: {command}"]


def test_destructive_and_outside_split_prose_from_listing(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    run_gate("terminal", {"command": "rm ../old.txt"})
    assert cards == [("Destructive command detected\n\nAccess outside working directory:",
                      ["- ../old.txt", "command: rm ../old.txt"])]


def test_terminal_absolute_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cat /etc/passwd"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_quoted_absolute_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": 'cat "/etc/passwd"'})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_terminal_windows_drive_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    command = f"type {Path.cwd().drive}\\Windows\\win.ini"
    ctx = run_gate("terminal", {"command": command})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_equals_value_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "tool --file=/etc/x"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_home_path_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cat ~/.gitconfig"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_cd_parent_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cd .. && ls"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_bare_cd_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cd && ls"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_semicolon_escape_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("terminal", {"command": "cat ../x; echo done"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_terminal_glued_redirect_rejected(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
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

    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    other = "z" if drive[0].lower() != "z" else "y"
    ctx = run_gate("terminal", {"command": f"cat /{other}/secret.txt"})
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_destructive_and_outside_prompts_once(monkeypatch):
    calls = []

    def answer(question, details):
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    ctx = run_gate("read_file", {"path": str(Path.cwd() / "a.txt")}, cwd=tmp_path)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_custom_cwd_terminal_absolute_path_does_not_prompt(monkeypatch, tmp_path):
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": f"cat {tmp_path / 'a.txt'}"}, cwd=tmp_path).blocked


def test_custom_cwd_terminal_workdir_path_outside_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
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
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: pytest.fail("must not ask"))
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
    outside = str(Path.cwd() / "a.txt")
    gate = build_gate(Rules(external_directory={"read_file": "deny"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside}, cwd=tmp_path)
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == (
        f"Permission denied by rule: Access outside working directory:\npath: {outside}")


def test_extension_vote_allow_overrides_default_ask():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
    ctx.decisions.append(_vote("allow", "trusted"))
    gate(ctx)
    assert not ctx.blocked


def test_conflict_rule_vs_extension_asks_and_yes_allows(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    ctx.decisions.append(_vote("deny", "no force push", source="nano_extension_guard"))
    gate(ctx)
    assert not ctx.blocked
    question, details = cards[0]
    assert question == "Decision conflict:"
    assert details == ["- rule: allow (^git)\n"
                       "- nano_extension_guard: deny (no force push)",
                       "command: git push --force"]


def test_conflict_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    ctx.decisions.append(_vote("deny", "no force push", source="nano_extension_guard"))
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_floor_beats_rule_and_extension(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: pytest.fail("floor must not ask"))
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
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "ask")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status"})
    gate(ctx)
    assert not ctx.blocked
    assert cards == [("^git", ["command: git status"])]


def test_rule_ask_rejected_denies_with_card(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    outside = str(Path.cwd().parent / "secret.txt")
    gate = build_gate(Rules(external_directory={"read_file": "ask"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"
    question, details = cards[0]
    assert question == "Access outside working directory:"
    assert details == [f"path: {outside}"]


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


def test_command_rule_severity_deny_beats_later_allow():
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "deny"), (re.compile("force"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: ^git"


def test_path_rule_deny_blocks_inside_workdir():
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"\.env$"), "deny")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": "config/.env"})
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: \.env$"


def test_path_rule_allow_outside_skips_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"^\.\./shared/"), "allow")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert not ctx.blocked


def test_path_rule_ask_prompts_with_pattern(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "n")
    gate = build_gate(Rules(paths={"write_file": [(re.compile(r"^dist/"), "ask")]}))
    ctx = BeforeToolContext(tool_name="write_file", args={"path": "dist/bundle.js"})
    gate(ctx)
    assert cards == ["^dist/"]
    assert ctx.block_reason == "Permission denied by user"


def test_external_deny_beats_path_allow():
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(
        paths={"read_file": [(re.compile(r"^\.\./shared/"), "allow")]},
        external_directory={"read_file": "deny"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.block_reason == (
        f"Permission denied by rule: Access outside working directory:\npath: {outside}")


def test_external_allow_loses_to_path_deny():
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(
        paths={"read_file": [(re.compile(r"^\.\./"), "deny")]},
        external_directory={"read_file": "allow"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: ^\.\./"


def test_terminal_external_directory_allow_skips_outside_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_terminal_external_directory_allow_skips_destructive_prompt(monkeypatch):
    # 决策 A：external allow 是规则票，整层跳过默认层（含 destructive 询问）
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm ../old.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_terminal_external_directory_deny_blocks():
    gate = build_gate(Rules(external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert ctx.block_reason == (
        "Permission denied by rule: Access outside working directory:\n- ../secret.txt")


def test_terminal_external_directory_message_lists_at_most_three_paths():
    gate = build_gate(Rules(external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal",
                            args={"command": "cat ../a ../b ../c ../d ../e"})
    gate(ctx)
    assert ctx.block_reason == (
        "Permission denied by rule: Access outside working directory:\n"
        "- ../a\n- ../b\n- ../c\n- ... and 2 more")


def test_terminal_external_directory_ask_prompts(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append((q, details)) or "n")
    gate = build_gate(Rules(external_directory={"terminal": "ask"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert cards == [("Access outside working directory:",
                      ["- ../secret.txt", "command: cat ../secret.txt"])]


def test_external_rule_ask_card_matches_default_layer(monkeypatch):
    """统一格式：external_directory 规则票与默认层的确认卡完全一致。"""
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    command = "cat ../a ../b"
    build_gate(Rules())(BeforeToolContext(tool_name="terminal", args={"command": command}))
    build_gate(Rules(external_directory={"terminal": "ask"}))(
        BeforeToolContext(tool_name="terminal", args={"command": command}))
    expected = ("Access outside working directory:",
                ["- ../a\n- ../b", f"command: {command}"])
    assert cards == [expected, expected]


def test_conflict_card_keeps_outside_listing_from_rule(monkeypatch):
    """冲突卡同样带规则层的越界清单：用户当场裁决时能看到触发了哪些路径。"""
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "n")
    gate = build_gate(Rules(external_directory={"terminal": "ask"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"},
                            decisions=[_vote("allow", "trusted")])
    gate(ctx)
    assert cards == [("Decision conflict:",
                      ["- rule: ask (Access outside working directory:)\n"
                       "- nano_extension_x: allow (trusted)",
                       "- ../secret.txt",
                       "command: cat ../secret.txt"])]


def test_terminal_external_directory_ignores_inside_command(monkeypatch):
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ./notes.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_command_ask_beats_external_allow(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append((q, details)) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^rm "), "ask")], external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm ../old.txt"})
    gate(ctx)
    # 越界命中被更严的 command ask 压过：details 不带越界清单
    assert cards == [("^rm ", ["command: rm ../old.txt"])]
    assert not ctx.blocked


def test_external_deny_beats_command_allow():
    gate = build_gate(Rules(terminal=[(re.compile("^cat "), "allow")],
                            external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert ctx.block_reason == (
        "Permission denied by rule: Access outside working directory:\n- ../secret.txt")


def test_external_allow_loses_to_command_deny():
    gate = build_gate(Rules(terminal=[(re.compile("secret"), "deny")],
                            external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: secret"


def test_path_rule_matches_resolved_symlink_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    workdir = tmp_path / "proj"
    workdir.mkdir()
    _link_dir(workdir / "link", outside)
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"^\.\./outside/"), "deny")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": "link/secret.txt"}, cwd=workdir)
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: ^\.\./outside/"


# --- approval_mode：Yolo 只执行地板校验，其余（规则 / 扩展票 / 默认层）全部跳过 ---

@pytest.mark.parametrize("tool_name,args", [
    ("terminal", {"command": "rm old.txt"}),          # 危险命令
    ("terminal", {"command": "cat ../secret.txt"}),   # 命令引用越界路径
    ("terminal", {"command": "rm ../old.txt"}),       # 两者同时命中
    ("read_file", {"path": str(Path.cwd().parent / "secret.txt")}),  # 文件工具越界
])
def test_yolo_allows_without_prompt(monkeypatch, tool_name, args):
    """Yolo 信任 LLM 的输出：危险命令与越界访问都不再询问。"""
    _with_mode(monkeypatch, "Yolo")
    _no_prompt(monkeypatch)
    assert not run_gate(tool_name, args).blocked


def test_yolo_still_denies_floor(monkeypatch):
    _with_mode(monkeypatch, "Yolo")
    _no_prompt(monkeypatch)
    ctx = run_gate("terminal", {"command": "sudo ls"})
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."


def test_yolo_skips_rule_vote(monkeypatch):
    """Yolo 下规则层整体跳过：deny 不拦、ask 不弹卡。"""
    _with_mode(monkeypatch, "Yolo")
    _no_prompt(monkeypatch)
    for action in ("deny", "ask"):
        ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
        build_gate(Rules(terminal=[(re.compile("^rm "), action)]))(ctx)
        assert not ctx.blocked, action


def test_yolo_skips_extension_votes(monkeypatch):
    """Yolo 下扩展表态跳过：deny 不拦、ask 不弹卡。"""
    _with_mode(monkeypatch, "Yolo")
    _no_prompt(monkeypatch)
    for action in ("deny", "ask"):
        ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"},
                                decisions=[_vote(action, "policy")])
        build_gate(Rules())(ctx)
        assert not ctx.blocked, action


def test_yolo_skips_external_rule_deny(monkeypatch):
    """Yolo 下 external_directory 规则同样不参与。"""
    _with_mode(monkeypatch, "Yolo")
    _no_prompt(monkeypatch)
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    build_gate(Rules(external_directory={"terminal": "deny"}))(ctx)
    assert not ctx.blocked


def test_approval_mode_is_read_live(monkeypatch):
    """每次裁决实时读配置：设置窗落盘后无需重启即可切换。"""
    manager = SimpleNamespace(config=AgentConfig(approval_mode="Manual"))
    monkeypatch.setattr(permission, "CONFIGMANAGER", manager)
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: "n")
    assert run_gate("terminal", {"command": "rm old.txt"}).blocked
    manager.config = AgentConfig(approval_mode="Yolo")
    _no_prompt(monkeypatch)
    assert not run_gate("terminal", {"command": "rm old.txt"}).blocked


@pytest.mark.parametrize("mode", ["LLM", "Decision Model"])
def test_unimplemented_modes_fall_back_to_manual(monkeypatch, mode):
    _with_mode(monkeypatch, mode)
    cards = []
    monkeypatch.setattr(permission, "ask_permission",
                        lambda q, details: cards.append(q) or "n")
    assert run_gate("terminal", {"command": "rm old.txt"}).blocked
    assert cards == ["Destructive command detected"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_read_file_url_does_not_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    assert not run_gate("read_file", {"path": "https://example.com/shot.png"}).blocked


def test_read_file_url_matches_path_rule_against_raw_url():
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"^https://"), "deny")]}))
    ctx = BeforeToolContext(tool_name="read_file",
                            args={"path": "https://example.com/shot.png"})
    gate(ctx)
    assert ctx.blocked and ctx.block_reason.startswith("Permission denied by rule:")
