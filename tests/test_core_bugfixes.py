"""core 重构回归：实现与意图不符的 6 处（rg 路径 NameError / grep 回退参数错位 /
list_tasks 三元优先级 / cron 未知 job KeyError / 会话索引写失败 NameError /
ddgs extract 返回类型不匹配）。"""
import asyncio
import shutil
import sys
import types

import pytest


def test_glob_async_with_ripgrep_lists_matches(monkeypatch, tmp_path):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")

    import core.tools.base_tools.glob as glob_mod

    monkeypatch.setattr(glob_mod, "has_ripgrep", lambda: True)
    monkeypatch.setattr(glob_mod, "WORKDIR", tmp_path)
    (tmp_path / "a.py").write_text("x", encoding="utf-8")

    assert "a.py" in asyncio.run(glob_mod.run_glob_async("*.py"))
    assert asyncio.run(glob_mod.run_glob_async("*.nope")) == "(No matches)"


def test_glob_no_matches_placeholder(monkeypatch, tmp_path):
    import core.tools.base_tools.glob as glob_mod

    monkeypatch.setattr(glob_mod, "WORKDIR", tmp_path)
    assert glob_mod.run_glob("*.nope") == "(No matches)"


def test_grep_async_python_fallback_searches(monkeypatch, tmp_path):
    import core.tools.base_tools.grep as grep_mod

    monkeypatch.setattr(grep_mod, "_find_grep_tool", lambda: "python")
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")

    out = asyncio.run(grep_mod.run_grep_async("needle", path=str(tmp_path), cwd=tmp_path))

    assert "needle" in out


def test_list_tasks_shows_tasks_without_worktree(monkeypatch, tmp_path):
    import core.experimental.task as task_mod
    from core.tools.extra_tools import task as task_tool

    monkeypatch.setattr(task_mod, "TASK_DIR", tmp_path)
    task_tool.run_create_task("write docs", "desc")

    out = task_tool.run_list_tasks()

    assert "write docs" in out and "[pending]" in out


def test_cancel_unknown_cron_reports_not_found():
    from core.cron_scheduler import cancel_job

    assert cancel_job("cron-nonexistent") == "Job cron-nonexistent not found"


def test_session_index_write_failure_returns_false(monkeypatch, tmp_path):
    import core.context.session.session as session_module

    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "missing" / "index.jsonl")
    manager = session_module.SessionManager()
    manager.new_session()

    assert manager._update_session_index() is False


class _FakeDDGS:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract(self, url):
        return {"url": url, "content": "page text"}


def test_ddgs_extract_returns_ok_and_content(monkeypatch):
    import core.tools.web_search.web_extract as web_extract

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=_FakeDDGS))

    ok, content = asyncio.run(web_extract.ddgs_extract("http://example.com"))

    assert ok is True
    assert content == "page text"
