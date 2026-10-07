"""core/tools/shell.py 的 shell 优先级探测、命令拼装与启动。"""
import asyncio
from pathlib import Path

import pytest

from core.tools import shell

_WSL_BASH = "C:\\Windows\\System32\\bash.exe"
_GIT_BASH = str(Path("Git") / "bin" / "bash.exe")
_PWSH = str(Path("PowerShell") / "7" / "pwsh.exe")


@pytest.fixture(autouse=True)
def _clear_shell_cache():
    """find_shell 被 @cache 记忆，monkeypatch 的 which 需要每次从零探测。"""
    shell.find_shell.cache_clear()


def test_is_wsl_bash_recognizes_launcher_paths():
    assert shell._is_wsl_bash(_WSL_BASH)
    assert shell._is_wsl_bash("C:/Windows/Sysnative/bash.exe")
    assert not shell._is_wsl_bash(_GIT_BASH)
    assert not shell._is_wsl_bash("/bin/bash")


def test_find_shell_falls_back_to_wsl_launcher(monkeypatch):
    monkeypatch.setattr(shell.shutil, "which", lambda name: _WSL_BASH if name == "bash" else None)
    assert shell.find_shell() == _WSL_BASH


def test_find_shell_uses_git_bash_beside_git(monkeypatch, tmp_path):
    git_bash = tmp_path / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.touch()
    git = tmp_path / "Git" / "cmd" / "git.exe"
    monkeypatch.setattr(shell.shutil, "which", lambda name: str(git) if name == "git" else None)
    assert shell.find_shell() == str(git_bash)


def test_find_shell_prefers_pwsh_over_powershell(monkeypatch):
    tools = {"pwsh": _PWSH, "powershell": str(Path("powershell.exe"))}
    monkeypatch.setattr(shell.shutil, "which", lambda name: tools.get(name))
    assert shell.find_shell() == _PWSH


def test_find_shell_probes_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shell.shutil, "which", lambda name: calls.append(name) or None)
    shell.find_shell()
    probed = len(calls)
    shell.find_shell()
    assert len(calls) == probed


def test_shell_kind_classifies_executables():
    assert shell.shell_kind(_GIT_BASH) == "bash"
    assert shell.shell_kind(_WSL_BASH) == "bash"
    assert shell.shell_kind(_PWSH) == "pwsh"
    assert shell.shell_kind("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe") == "powershell"
    assert shell.shell_kind(None) is None
    assert shell.shell_kind("C:/tools/cmd.exe") is None


def test_build_command_invocation_bash(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: _GIT_BASH)
    assert shell.build_command_invocation("ls") == shell.ShellInvocation(False, [_GIT_BASH, "-c", "ls"], None)


def test_build_command_invocation_wsl_bash_pipes_script_via_stdin(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: _WSL_BASH)
    assert shell.build_command_invocation("ls") == shell.ShellInvocation(False, [_WSL_BASH, "-s"], "ls\n")


def test_build_command_invocation_pwsh_sets_utf8_output(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: _PWSH)
    assert shell.build_command_invocation("dir") == shell.ShellInvocation(
        False, [_PWSH, "-NoProfile", "-Command", shell._PS_UTF8_PREFIX + "dir"], None)


def test_build_command_invocation_falls_back_to_system_shell(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: None)
    assert shell.build_command_invocation("dir") == shell.ShellInvocation(True, ["dir"], None)


def test_quote_argument_escapes_posix_shell_metacharacters():
    assert shell._quote_argument("plain", _GIT_BASH) == "plain"
    assert shell._quote_argument("a b&c;$(x)", _GIT_BASH) == "'a b&c;$(x)'"
    assert shell._quote_argument("it's", _GIT_BASH) == "'it'\"'\"'s'"


def test_quote_argument_escapes_powershell_metacharacters():
    assert shell._quote_argument("plain", _PWSH) == "'plain'"
    assert shell._quote_argument("a b&c;$(x)", _PWSH) == "'a b&c;$(x)'"
    assert shell._quote_argument("it's", _PWSH) == "'it''s'"


def test_build_argv_invocation_quotes_each_arg_for_bash(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: _GIT_BASH)
    url = "https://x/?a=1&b=it's"
    assert shell.build_argv_invocation(["npx", "defuddle", "parse", url, "--md"]) == shell.ShellInvocation(
        False, [_GIT_BASH, "-c", "npx defuddle parse 'https://x/?a=1&b=it'\"'\"'s' --md"], None)


def test_build_argv_invocation_quotes_each_arg_for_powershell(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: _PWSH)
    url = "https://x/?a=1&b=it's"
    assert shell.build_argv_invocation(["npx", "defuddle", "parse", url, "--md"]) == shell.ShellInvocation(
        False, [_PWSH, "-NoProfile", "-Command",
                shell._PS_UTF8_PREFIX + "& 'npx' 'defuddle' 'parse' 'https://x/?a=1&b=it''s' '--md'"], None)


def test_build_argv_invocation_without_preferred_shell_keeps_argv_separated(monkeypatch):
    monkeypatch.setattr(shell, "find_shell", lambda: None)
    assert shell.build_argv_invocation(["npx", "defuddle", "parse", "https://x/?a=1&b=2", "--md"],
                                       executable_path="E:/nodejs/npx.CMD") == shell.ShellInvocation(
        False, ["E:/nodejs/npx.CMD", "defuddle", "parse", "https://x/?a=1&b=2", "--md"], None)


class _FakeWriteStream:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeWriteStream()
        self.returncode = 0


def test_start_process_execs_shell_and_feeds_stdin_script(monkeypatch):
    captured = {}
    process = _FakeProcess()

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(shell.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(shell, "find_shell", lambda: _WSL_BASH)
    result = asyncio.run(shell.start_process(shell.build_command_invocation("echo hi"), cwd="/tmp"))

    assert result is process
    assert captured["argv"] == (_WSL_BASH, "-s")
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == "/tmp"
    assert process.stdin.data == b"echo hi\n"
    assert process.stdin.closed is True


def test_start_process_uses_system_shell_without_stdin_script(monkeypatch):
    captured = {}
    process = _FakeProcess()

    async def fake_shell(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(shell.asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(shell, "find_shell", lambda: None)
    result = asyncio.run(shell.start_process(shell.build_command_invocation("dir")))

    assert result is process
    assert captured["command"] == "dir"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
    assert process.stdin.data == b""


def test_run_terminal_async_uses_shared_shell_invocation(monkeypatch):
    import core.tools.base_tools.terminal as terminal

    captured = {}

    class _Process:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_start_process(invocation, cwd=None):
        captured["invocation"] = invocation
        return _Process()

    monkeypatch.setattr(shell, "find_shell", lambda: _GIT_BASH)
    monkeypatch.setattr(terminal, "start_process", fake_start_process)

    assert asyncio.run(terminal.run_terminal_async("echo ok")) == "ok"
    assert captured["invocation"] == shell.build_command_invocation("echo ok")
