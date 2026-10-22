"""glob 工具：ripgrep 与 Python 回退两条分支返回同一结果集，隐藏 / .gitignore 由 use_ignore 决定。"""
import asyncio
import shutil
import subprocess
import sys

import pytest

import core.tools.base_tools.glob as glob_mod


def _build(root, *, git: bool) -> None:
    (root / "src" / "nested").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / ".hidden").mkdir()
    (root / "top.py").write_text("x", encoding="utf-8")
    (root / "src" / "a.ts").write_text("x", encoding="utf-8")
    (root / "src" / "nested" / "deep.py").write_text("x", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / ".hidden" / "h.py").write_text("x", encoding="utf-8")
    (root / "node_modules" / "x.py").write_text("x", encoding="utf-8")
    (root / "ignored.py").write_text("x", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.py\nnode_modules/\n", encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)


def _glob(monkeypatch, root, pattern: str, use_ignore: bool, *, ripgrep: bool) -> str:
    monkeypatch.setattr(glob_mod, "has_ripgrep", lambda: ripgrep)
    monkeypatch.setattr(glob_mod, "WORKDIR", root)
    return asyncio.run(glob_mod.Glob(pattern=pattern, use_ignore=use_ignore).arun())


def _lines(out: str) -> list[str]:
    return out.splitlines()


def _git_repo(tmp_path) -> None:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    _build(tmp_path, git=True)


def test_default_skips_hidden_and_gitignored(monkeypatch, tmp_path):
    """默认（use_ignore=True）跳过隐藏路径与 .gitignore 命中项，只回文件、路径相对且排序。"""
    _git_repo(tmp_path)

    assert _lines(_glob(monkeypatch, tmp_path, "*", True, ripgrep=False)) == [
        "src/a.ts", "src/nested/deep.py", "top.py"]


def test_pattern_without_slash_matches_at_any_depth(monkeypatch, tmp_path):
    """无 `/` 的模式按文件名在任意深度匹配（`*.py` == `**/*.py`）。"""
    _build(tmp_path, git=False)

    assert _lines(_glob(monkeypatch, tmp_path, "*.py", True, ripgrep=False)) == [
        "ignored.py", "node_modules/x.py", "src/nested/deep.py", "top.py"]


def test_diamond_pattern_within_segment(monkeypatch, tmp_path):
    """`*` 不跨 `/`：`src/*.ts` 只命中一层，`src/**/*.ts` 才递归。"""
    _build(tmp_path, git=False)

    assert _lines(_glob(monkeypatch, tmp_path, "src/*.ts", True, ripgrep=False)) == ["src/a.ts"]
    assert _lines(_glob(monkeypatch, tmp_path, "src/**/*.ts", True, ripgrep=False)) == ["src/a.ts"]


def test_use_ignore_false_includes_hidden_and_ignored(monkeypatch, tmp_path):
    """use_ignore=False 同时放开隐藏与忽略规则，两条分支结果一致。"""
    _build(tmp_path, git=False)

    python = set(_lines(_glob(monkeypatch, tmp_path, "*", False, ripgrep=False)))
    assert {"top.py", ".env", ".hidden/h.py", "ignored.py", "node_modules/x.py", ".gitignore"} <= python
    if shutil.which("rg"):
        assert _lines(_glob(monkeypatch, tmp_path, "*", False, ripgrep=True)) == sorted(python)


def test_branches_agree_on_every_pattern(monkeypatch, tmp_path):
    """同一模式下 ripgrep 分支与 Python 回退分支输出逐字节相同。"""
    _git_repo(tmp_path)
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")

    for pattern in ("*", "*.py", "**/*.py", "src/*", "src/**/*.ts", "src/nested/*.py"):
        expected = _glob(monkeypatch, tmp_path, pattern, True, ripgrep=False)
        assert _glob(monkeypatch, tmp_path, pattern, True, ripgrep=True) == expected, pattern


def test_nested_gitignore_negation(monkeypatch, tmp_path):
    """子目录 .gitignore 与其 `!` 取反同样生效，两条分支一致。"""
    _git_repo(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".gitignore").write_text("*.log\n!keep.log\n", encoding="utf-8")
    (tmp_path / "sub" / "a.log").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "keep.log").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "b.py").write_text("x", encoding="utf-8")

    expected = ["sub/b.py", "sub/keep.log"]
    assert _lines(_glob(monkeypatch, tmp_path, "sub/*", True, ripgrep=False)) == expected
    if shutil.which("rg"):
        assert _lines(_glob(monkeypatch, tmp_path, "sub/*", True, ripgrep=True)) == expected


def test_branches_agree_without_git_repo(monkeypatch, tmp_path):
    """非 git 仓库里 .gitignore 不生效（与 rg 一致），两条分支仍然相同。"""
    _build(tmp_path, git=False)
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")

    for pattern in ("*", "*.py"):
        expected = _glob(monkeypatch, tmp_path, pattern, True, ripgrep=False)
        assert _glob(monkeypatch, tmp_path, pattern, True, ripgrep=True) == expected, pattern
    assert "ignored.py" in _lines(_glob(monkeypatch, tmp_path, "*", True, ripgrep=False))


def test_parent_gitignore_applies_when_cwd_is_subdir(monkeypatch, tmp_path):
    """工作目录在仓库子目录时，父目录 .gitignore 仍生效，两条分支一致。"""
    _git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.log").write_text("x", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("x", encoding="utf-8")
    sub = tmp_path / "pkg"

    assert _lines(_glob(monkeypatch, sub, "*", True, ripgrep=False)) == ["b.py"]
    if shutil.which("rg"):
        assert _lines(_glob(monkeypatch, sub, "*", True, ripgrep=True)) == ["b.py"]


def test_directories_are_not_returned(monkeypatch, tmp_path):
    _build(tmp_path, git=False)

    lines = _lines(_glob(monkeypatch, tmp_path, "*", True, ripgrep=False))
    assert "src" not in lines and "node_modules" not in lines


def test_no_matches_placeholder(monkeypatch, tmp_path):
    _build(tmp_path, git=False)

    assert _glob(monkeypatch, tmp_path, "*.nope", True, ripgrep=False) == "(No matches)"
    if shutil.which("rg"):
        assert _glob(monkeypatch, tmp_path, "*.nope", True, ripgrep=True) == "(No matches)"


def test_leading_slash_anchors_to_workdir(monkeypatch, tmp_path):
    """以 `/` 开头的模式锚定在工作目录根，不因无 `/` 而退化成任意深度。"""
    _build(tmp_path, git=False)

    assert _lines(_glob(monkeypatch, tmp_path, "/top.py", True, ripgrep=False)) == ["top.py"]
    assert _glob(monkeypatch, tmp_path, "/deep.py", True, ripgrep=False) == "(No matches)"


def test_branches_agree_in_git_repo_with_use_ignore_false(monkeypatch, tmp_path):
    """git 仓库里 use_ignore=False 时两支一致（含 .git 内容、隐藏与忽略项）。"""
    _git_repo(tmp_path)
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")

    python = _glob(monkeypatch, tmp_path, "*", False, ripgrep=False)
    assert _glob(monkeypatch, tmp_path, "*", False, ripgrep=True) == python
    assert "ignored.py" in _lines(python)


def test_gitignore_negated_dotfile_stays_hidden(monkeypatch, tmp_path):
    """.gitignore 取反放出的隐藏文件仍按「隐藏」跳过，两支一致（rg 的 walker 会把它吐出来）。"""
    _git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".*\n!.gitattributes\n", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("x", encoding="utf-8")

    python = _lines(_glob(monkeypatch, tmp_path, "*", True, ripgrep=False))
    assert ".gitattributes" not in python
    if shutil.which("rg"):
        assert _lines(_glob(monkeypatch, tmp_path, "*", True, ripgrep=True)) == python

    python_all = _lines(_glob(monkeypatch, tmp_path, "*", False, ripgrep=False))
    assert ".gitattributes" in python_all
    if shutil.which("rg"):
        assert _lines(_glob(monkeypatch, tmp_path, "*", False, ripgrep=True)) == python_all


@pytest.mark.skipif(sys.platform != "win32", reason="反斜杠模式归一仅 Windows")
def test_backslash_pattern_is_normalized_on_windows(monkeypatch, tmp_path):
    _build(tmp_path, git=False)

    assert _lines(_glob(monkeypatch, tmp_path, r"src\*.ts", True, ripgrep=False)) == ["src/a.ts"]


def test_use_ignore_is_part_of_the_tool_schema():
    """use_ignore 由 LLM 决定，必须是工具参数而不是内部开关。"""
    schema = glob_mod.Glob.to_openai_tool()["function"]
    assert schema["parameters"]["properties"]["use_ignore"]["default"] is True
    assert "use_ignore" in schema["description"]
