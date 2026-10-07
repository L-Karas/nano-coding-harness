"""defuddle_extract 与 terminal 工具共用 core.tools.shell 的优先 shell 与启动方式。"""
import asyncio
from pathlib import Path

import core.tools.web_search.web_extract as web_extract
from core.tools import shell

_GIT_BASH = str(Path("Git") / "bin" / "bash.exe")
_NPX = "E:/nodejs/npx.CMD"


class _FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self.stdout, self.stderr


def _patch_start_process(monkeypatch, process, captured):
    async def fake_start_process(invocation, cwd=None):
        captured["invocation"] = invocation
        return process

    monkeypatch.setattr(web_extract, "start_process", fake_start_process)
    monkeypatch.setattr(web_extract.shutil, "which", lambda name: _NPX)


def test_defuddle_extract_runs_through_preferred_shell(monkeypatch):
    captured = {}
    _patch_start_process(monkeypatch, _FakeProcess(stdout=b"# page"), captured)
    monkeypatch.setattr(shell, "find_shell", lambda: _GIT_BASH)

    ok, content = asyncio.run(web_extract.defuddle_extract("https://x/?a=1&b=2"))

    assert (ok, content) == (True, "# page")
    assert captured["invocation"] == shell.ShellInvocation(
        False, [_GIT_BASH, "-c", "npx defuddle parse 'https://x/?a=1&b=2' --md"], None)


def test_defuddle_extract_keeps_argv_separated_without_preferred_shell(monkeypatch):
    captured = {}
    _patch_start_process(monkeypatch, _FakeProcess(stdout=b"# page"), captured)
    monkeypatch.setattr(shell, "find_shell", lambda: None)

    ok, _ = asyncio.run(web_extract.defuddle_extract("https://x/?a=1&b=2"))

    assert ok is True
    assert captured["invocation"] == shell.ShellInvocation(
        False, [_NPX, "defuddle", "parse", "https://x/?a=1&b=2", "--md"], None)


def test_defuddle_extract_reports_shell_stderr(monkeypatch):
    captured = {}
    _patch_start_process(monkeypatch, _FakeProcess(stderr=b"npx: not found", returncode=1), captured)
    monkeypatch.setattr(shell, "find_shell", lambda: _GIT_BASH)

    ok, content = asyncio.run(web_extract.defuddle_extract("https://example.com"))

    assert ok is False
    assert "npx: not found" in content
