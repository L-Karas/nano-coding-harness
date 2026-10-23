"""file_walk 模块：glob / grep 共用的 rg 语义文件枚举（隐藏 / .gitignore / 符号链接）。

回归点：rg 是基准——仓库外的 .gitignore 不参与（`require_git`）、`!` 取反按浅→深覆盖、
`skip_hidden_and_ignored=False` 放开隐藏与忽略、符号链接不跟随。
"""
import os
from pathlib import Path

import pytest

from core.tools.file_walk import iter_files


def _names(root, **kwargs) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in iter_files(root, **kwargs))


def _build(root) -> None:
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x", encoding="utf-8")
    (root / "top.py").write_text("x", encoding="utf-8")
    (root / ".hidden.py").write_text("x", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "ignored.log").write_text("x", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")


def test_default_skips_hidden_and_gitignored(tmp_path):
    _build(tmp_path)

    assert _names(tmp_path) == ["src/a.py", "top.py"]


def test_gitignore_outside_a_repo_is_inert(tmp_path):
    """没有 .git 目录时 .gitignore 不参与（与 rg 的 require_git 一致）。"""
    (tmp_path / "a.log").write_text("x", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")

    assert _names(tmp_path) == ["a.log"]


def test_negation_in_deeper_gitignore_wins(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".gitignore").write_text("!keep.log\n", encoding="utf-8")
    (tmp_path / "sub" / "drop.log").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "keep.log").write_text("x", encoding="utf-8")

    assert _names(tmp_path) == ["sub/keep.log"]


def test_skip_hidden_and_ignored_false_includes_hidden_and_ignored(tmp_path):
    _build(tmp_path)

    assert _names(tmp_path, skip_hidden_and_ignored=False) == [
        ".git/config", ".gitignore", ".hidden.py", "ignored.log", "src/a.py", "top.py"]


def test_symlinks_are_skipped(tmp_path):
    """rg 默认不跟随符号链接：链接文件与链接目录都不该出现在候选里。"""
    (tmp_path / "real.py").write_text("x", encoding="utf-8")
    try:
        os.symlink(tmp_path / "real.py", tmp_path / "link.py")
        os.symlink(tmp_path, tmp_path / "linkdir", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows 无权限时为 WinError 1314
        pytest.skip(f"symlink unavailable: {exc}")

    assert _names(tmp_path) == ["real.py"]


def test_symlinked_files_are_skipped_without_os_support(monkeypatch, tmp_path):
    """无权限建软链的平台上也要钉住 `is_symlink()` 这个过滤（否则该分支只在 POSIX CI 上被验证）。"""
    (tmp_path / "real.py").write_text("x", encoding="utf-8")
    (tmp_path / "link.py").write_text("x", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link.py" or original(self))

    assert _names(tmp_path) == ["real.py"]
