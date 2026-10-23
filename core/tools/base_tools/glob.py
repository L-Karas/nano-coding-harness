import asyncio
import glob as g
import os
import re
from pathlib import Path

from pydantic import Field

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.tools.file_walk import iter_files, relpath
from core.tools.ripgrep import has_ripgrep, run_rg
from core.tools.tool_base import BaseTool

DESCRIPTION = (
    "Find files by glob pattern; use `grep` for file contents. Patterns are relative to the working directory "
    "(`*` and `?` don't cross `/`, `**` does; a pattern without `/` matches file names at any depth). Returns "
    "matching paths relative to the working directory; `(No matches)` if none. "
    "Hidden and git-ignored paths are skipped unless `skip_hidden_and_ignored` is false."
)


class Glob(BaseTool):
    __doc__ = DESCRIPTION
    pattern: str = Field(
        description="Glob pattern relative to the working directory, e.g. '*.py' or "
                    "'src/**/*.ts'."
    )
    skip_hidden_and_ignored: bool = Field(
        default=True,
        description="Skip hidden and git-ignored paths (default: true)."
    )

    agent_type: set = {"main", "sub-agent"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        root = _root(tctx)
        # 回退分支：候选文件由 file_walk 枚举（隐藏 / .gitignore / 符号链接的取舍见该模块）
        return _format(_match([relpath(root, path) for path in iter_files(root, self.skip_hidden_and_ignored)],
                              self.pattern, self.skip_hidden_and_ignored))

    async def arun(self, tctx: ToolContext | None = None) -> str:
        if tctx:
            tctx.raise_if_cancelled()

        if not has_ripgrep():
            return await asyncio.to_thread(self.run, tctx)

        root = _root(tctx)
        args = ["--files", "-0"]
        if not self.skip_hidden_and_ignored:
            args += ["--no-ignore", "--hidden"]

        stdout, stderr, returncode = await run_rg([*args, str(root)], tctx)
        if returncode not in (0, 1):
            raise RuntimeError(stderr or f"rg --files failed with exit code {returncode}")

        paths = [relpath(root, line) for line in stdout.split("\0") if line]
        return _format(_match(paths, self.pattern, self.skip_hidden_and_ignored))


def _root(tctx: ToolContext | None) -> Path:
    return tctx.cwd if tctx and tctx.cwd else WORKDIR


def _matcher(pattern: str, skip_hidden_and_ignored: bool) -> re.Pattern[str]:
    """pattern → 相对路径正则：`*` / `?` 不跨 `/`，`**` 跨目录。

    `include_hidden` 与 `iter_files` 的 dot 剪枝是同一层保险：rg 的 walker 在 `.gitignore` 用 `!` 取反放出
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
    return re.compile(g.translate(pat, recursive=True, include_hidden=not skip_hidden_and_ignored) if pat else r"(?!)")


def _match(paths: list[str], pattern: str, skip_hidden_and_ignored: bool) -> list[str]:
    """两条分支共用的过滤：按 pattern 匹配、去重、排序。"""
    matcher = _matcher(pattern, skip_hidden_and_ignored)
    return sorted({path for path in paths if matcher.fullmatch(path)})


def _format(matches: list[str]) -> str:
    return "\n".join(matches) if matches else "(No matches)"
