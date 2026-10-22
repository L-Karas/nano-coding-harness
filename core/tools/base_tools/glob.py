import asyncio
import glob as g
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text

DESCRIPTION = (
    "Find files by glob pattern; use `grep` for file contents. Patterns are relative to the working directory "
    "(`*` and `?` don't cross `/`, `**` does; a pattern without `/` matches file names at any depth). Returns "
    "matching paths relative to the working directory; `(No matches)` if none. "
    "Hidden and git-ignored paths are skipped unless `use_ignore` is false."
)


class Glob(BaseTool):
    __doc__ = DESCRIPTION
    pattern: str = Field(
        description="Glob pattern relative to the working directory, e.g. '*.py' or "
                    "'src/**/*.ts'."
    )
    use_ignore: bool = Field(
        default=True,
        description="Skip hidden and git-ignored paths (default: true)."
    )

    agent_type: set = {"main", "sub-agent"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        return _format(_match(_walk(_root(tctx), self.use_ignore), self.pattern, self.use_ignore))

    async def arun(self, tctx: ToolContext | None = None) -> str:
        if tctx:
            tctx.raise_if_cancelled()

        if not has_ripgrep():
            return await asyncio.to_thread(self.run, tctx)

        root = _root(tctx)
        command = ["rg", "--files", "-0"]
        if not self.use_ignore:
            command += ["--no-ignore", "--hidden"]
        command += [str(root)]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await process.communicate()
            if tctx:
                tctx.raise_if_cancelled()
            if process.returncode not in (0, 1):
                raise RuntimeError(_to_text(stderr))

            paths = [_relpath(root, _to_text(line)) for line in stdout.split(b"\0") if line]
        except asyncio.CancelledError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

        return _format(_match(paths, self.pattern, self.use_ignore))


def has_ripgrep() -> bool:
    return bool(shutil.which("rg"))


def _root(tctx: ToolContext | None) -> Path:
    return tctx.cwd if tctx and tctx.cwd else WORKDIR


def _matcher(pattern: str, use_ignore: bool) -> re.Pattern[str]:
    """pattern → 相对路径正则：`*` / `?` 不跨 `/`，`**` 跨目录。

    `include_hidden` 与 `_walk` 的 dot 剪枝是同一层保险：rg 的 walker 在 `.gitignore` 用 `!` 取反放出
    隐藏文件时仍会把它吐出来，靠这里对齐。
    无 `/` 且不以 `/` 锚定的模式按文件名匹配，等价于在最前面补 `**/`。
    """
    pat = pattern.strip()
    if os.sep != "/":  # Windows 上手写的 `src\*.py` 归一成 `/`
        pat = pat.replace(os.sep, "/")
    rooted = pat.startswith("/")
    pat = pat.lstrip("/")
    while pat.startswith("./"):
        pat = pat[2:]
    if pat and pat != "**" and "/" not in pat and not rooted:
        pat = "**/" + pat
    return re.compile(g.translate(pat, recursive=True, include_hidden=not use_ignore) if pat else r"(?!)")


def _match(paths: list[str], pattern: str, use_ignore: bool) -> list[str]:
    """两条分支共用的过滤：按 pattern 匹配、去重、排序。"""
    matcher = _matcher(pattern, use_ignore)
    return sorted({path for path in paths if matcher.fullmatch(path)})


def _walk(root: Path, use_ignore: bool) -> list[str]:
    """Python 回退：按 `use_ignore` 枚举候选文件；忽略规则只看 .gitignore（rg 还读 .ignore / 全局 excludes）。"""
    rules = _IgnoreRules(root) if use_ignore else None
    files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _relpath(root, dirpath)
        if rules is not None:
            rules.load(rel_dir)
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and not rules.ignored(_join(rel_dir, d), True)]
            filenames = [f for f in filenames
                         if not f.startswith(".") and not rules.ignored(_join(rel_dir, f), False)]
        files.extend(_join(rel_dir, name) for name in filenames)

    return files


def _relpath(root: Path, path: str | Path) -> str:
    """相对 `root` 的 `/` 分隔路径；`root` 自身返回空串。"""
    rel = Path(os.path.relpath(path, root)).as_posix()
    return "" if rel == "." else rel


def _join(dir_rel: str, name: str) -> str:
    return f"{dir_rel}/{name}" if dir_rel else name


def _format(matches: list[str]) -> str:
    return "\n".join(matches) if matches else "(No matches)"


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


class _IgnoreRules:
    """最小 .gitignore 语义：注释 / 空行 / `!` 取反 / 结尾 `/` 仅目录 / 含 `/` 锚定 / 通配符 / 子目录叠加。

    只在 git 仓库内生效（与 rg 一致：仓库外的 .gitignore 不参与）；祖先目录的 .gitignore 也读入。
    """

    def __init__(self, root: Path):
        self._repo = _repo_root(root)
        self._rules: list[_Rule] = []
        self._prefix = _relpath(self._repo, root) if self._repo is not None else ""

        parts = self._prefix.split("/") if self._prefix else []
        for i in range(len(parts)):  # repo root → root 的父目录；root 自身由 walk 读到
            base = "/".join(parts[:i])
            self._load(self._repo / base if base else self._repo, base)

    def load(self, dir_rel: str) -> None:
        """读 `dir_rel` 目录下的 .gitignore（缺少则什么也不做），规则按加载顺序后写覆盖先写。"""
        if self._repo is None:
            return
        base = _join(self._prefix, dir_rel)
        self._load(self._repo / base if base else self._repo, base)

    def ignored(self, rel: str, is_dir: bool) -> bool:
        if self._repo is None:
            return False
        path = _join(self._prefix, rel)
        ignored = False
        for rule in self._rules:
            if rule.matches(path, is_dir):
                ignored = not rule.negated
        return ignored

    def _load(self, path: Path, base: str) -> None:
        gitignore = path / ".gitignore"
        if not gitignore.is_file():
            return
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            rule = _parse_rule(line, base)
            if rule is not None:
                self._rules.append(rule)


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
