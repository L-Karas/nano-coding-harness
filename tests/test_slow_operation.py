"""is_slow_operation 回归：只认命令位置的命令词，参数/路径/字符串里的关键词不算慢。"""

import pytest

from core.background_task import is_slow_operation

SLOW = [
    "make -j4",
    "pytest -x",
    "sleep 30",
    "pip install requests",
    "sudo npm install",
    "docker build -t x .",
    "FOO=1 cargo build",
    "cd /tmp && npm ci",
    "uv sync",
    "go test ./...",
    "./build.sh && ./install.sh",
    "time make",
]
FAST = [
    "echo install",
    "cat build.log",
    "ls build",
    "grep -rn make .",
    'git commit -m "fix build"',
    "cd build && node index.js",
    "test -f dist/app.js",
    "npm run lint",
]


@pytest.mark.parametrize("command", SLOW)
def test_slow_commands_run_in_background(command):
    assert is_slow_operation("terminal", {"command": command}), command


@pytest.mark.parametrize("command", FAST)
def test_fast_commands_stay_foreground(command):
    assert not is_slow_operation("terminal", {"command": command}), command


def test_non_terminal_tool_is_never_slow():
    assert not is_slow_operation("read", {"command": "make"})
