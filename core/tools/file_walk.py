"""rg 语义的候选文件枚举：隐藏 / .gitignore / 符号链接的取舍集中在这里，`glob` 与 `grep` 共用。

以 rg 默认值为基准：隐藏路径、`.gitignore` 命中项、符号链接都不进候选；`.gitignore` 只在 git
仓库内生效（对应 rg 的 `require_git`），祖先目录的规则也读入，`!` 取反按浅→深覆盖。rg 还读
`.ignore` / `.git/info/exclude` / 全局 excludes，这里只实现 `.gitignore`。
"""
import glob as g
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


def iter_files(root: Path, use_ignore: bool = True) -> Iterator[Path]:
    """枚举 `root` 下的文件（不含目录）；`use_ignore` 时跳过隐藏与 `.gitignore` 命中项。"""
    rules = _IgnoreRules(root) if use_ignore else None

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = relpath(root, dirpath)
        if rules is not None:
            rules.load(rel_dir)
            dirnames[:] = [name for name in dirnames
                           if not is_hidden(name) and not rules.ignored(_join(rel_dir, name), True)]
        for name in filenames:
            if rules is not None and (is_hidden(name) or rules.ignored(_join(rel_dir, name), False)):
                continue
            candidate = Path(dirpath) / name
            if not candidate.is_symlink():  # rg 默认不跟随符号链接
                yield candidate


def is_hidden(rel: str) -> bool:
    """相对路径里是否有 `.` 开头的片段（`.` / `..` 不算）。"""
    return any(part.startswith(".") and part not in (".", "..") for part in rel.split("/"))


def relpath(root: Path, path: str | Path) -> str:
    """相对 `root` 的 `/` 分隔路径；`root` 自身返回空串。"""
    rel = Path(os.path.relpath(path, root)).as_posix()
    return "" if rel == "." else rel


def _join(dir_rel: str, name: str) -> str:
    """拼 `/` 路径；任一侧为空时返回另一侧（`_join("", "a") == "a"`，`_join("a", "") == "a"`）。"""
    if not dir_rel:
        return name
    return f"{dir_rel}/{name}" if name else dir_rel


class _IgnoreRules:
    """最小 .gitignore 语义：注释 / 空行 / `!` 取反 / 结尾 `/` 仅目录 / 含 `/` 锚定 / 通配符 / 子目录叠加。"""

    def __init__(self, root: Path):
        self._repo = _repo_root(root)
        self._prefix = relpath(self._repo, root) if self._repo is not None else ""
        self._rules: list[_Rule] = []
        if self._repo is None:
            return

        # 仓库根 → root 的父目录；root 自身的 .gitignore 由 iter_files 遍历到时加载
        parts = self._prefix.split("/") if self._prefix else []
        for i in range(len(parts)):
            self._load("/".join(parts[:i]))

    def load(self, dir_rel: str) -> None:
        """读 `dir_rel` 目录下的 .gitignore；遍历顺序（浅→深）保证后写规则覆盖先写。"""
        if self._repo is not None:
            self._load(_join(self._prefix, dir_rel))

    def ignored(self, rel: str, is_dir: bool) -> bool:
        if self._repo is None:
            return False
        path = _join(self._prefix, rel)
        ignored = False
        for rule in self._rules:
            if rule.matches(path, is_dir):
                ignored = not rule.negated
        return ignored

    def _load(self, repo_rel: str) -> None:
        """`repo_rel` 相对仓库根。"""
        if self._repo is None:
            return
        base = self._repo / repo_rel if repo_rel else self._repo
        gitignore = base / ".gitignore"
        if not gitignore.is_file():
            return
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            rule = _parse_rule(line, repo_rel)
            if rule is not None:
                self._rules.append(rule)


def _repo_root(root: Path) -> Path | None:
    for path in (root, *root.parents):
        if (path / ".git").exists():
            return path
    return None


@dataclass(frozen=True)
class _Rule:
    """一条 .gitignore 规则；`base` 是规则所在目录相对仓库根的路径。"""
    base: str
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    anchored: bool

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.base:
            prefix = self.base + "/"
            if not path.startswith(prefix):
                return False
            path = path[len(prefix):]
        target = path if self.anchored else path.rsplit("/", 1)[-1]
        return self.regex.fullmatch(target) is not None


def _parse_rule(line: str, base: str) -> _Rule | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    negated = line.startswith("!")
    if negated:
        line = line[1:]
    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]
    anchored = line.startswith("/") or "/" in line
    line = line.lstrip("/")
    if not line:
        return None
    return _Rule(
        base=base,
        regex=re.compile(g.translate(line, recursive=True, include_hidden=True)),
        negated=negated, dir_only=dir_only, anchored=anchored
    )
