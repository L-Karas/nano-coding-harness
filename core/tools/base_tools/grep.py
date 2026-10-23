"""grep 工具：ripgrep 分支 + Python 回退分支，语义以 rg 的默认值为基准。

回退分支复用 `file_walk.iter_files` 的候选取舍，并按 rg 的行模型处理内容：只按 `\\n` 切行、
UTF-16 BOM 先转码、其余 UTF-8 有损解码、含 NUL 的文件当二进制。两分支的候选取舍与输出格式
一致，残差集中在正则引擎、二进制探测与告警：POSIX 字符类两侧语义不同，无效 UTF-8 字节在
回退分支按 `U+FFFD` 参与匹配，rg 只在读到的缓冲区里发现 NUL，不可读路径只有 rg 分支会附
`(Search incomplete: ...)`。

`pattern` 以 Python `re` 为准：rg 编译不了的 lookaround / 反向引用会退回 Python 分支。
没有 `grep` 分支：GNU ERE 的语法与 rg / Python 不同，也读不了 `.gitignore`、剥不掉 BOM；
`.gitignore` 之外 rg 还读的 `.ignore` / 全局 excludes 未实现。
"""
import asyncio
import codecs
import io
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.tools.file_walk import is_hidden, iter_files, relpath
from core.tools.ripgrep import has_ripgrep, run_rg
from core.tools.tool_base import BaseTool

DESCRIPTION = (
    "Search for a pattern in files under `path` (a directory, or a single file), returning matching lines with "
    "file path, line number (1-indexed), and content. `pattern` uses Python `re` syntax and is validated up "
    "front. Files containing a NUL byte are not searched; hidden and git-ignored files are skipped unless "
    "`use_ignore` is false."
)

_MATCH_RE = re.compile(r"^(.*?):(\d+):(.*)$")
_MATCH_LIMIT = 50


@dataclass(frozen=True)
class _Query:
    """一次搜索的输入；两个引擎与记录过滤器共用。"""
    pattern: str
    file_pattern: str
    use_ignore: bool


@dataclass(frozen=True)
class _Match:
    """一条命中记录：绝对路径、1-based 行号、已规范化的内容。"""
    path: str
    line_no: int
    content: str


class Grep(BaseTool):
    __doc__ = DESCRIPTION
    pattern: str = Field(description="The regex pattern to search for in file contents.")
    path: str = Field(default="", description="The directory or file to search in. Defaults to the current working "
                                              "directory.")
    file_pattern: str = Field(default="*", description="Glob matched against each candidate path (e.g. '*.py' or "
                                                       "'src/*.py'; case-insensitive on Windows); '*' keeps every "
                                                       "file.")
    use_ignore: bool = Field(default=True, description="Skip hidden and git-ignored paths while walking "
                                                      "(default: true).")

    agent_type: set = {"main", "sub-agent"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        """同步入口：不起子进程，直接走 Python 回退分支。"""
        path = _validate_path(self.path, tctx.cwd if tctx else None)
        query = _Query(self.pattern, self.file_pattern, self.use_ignore)
        return _format_matches(_search_python(path, query))

    async def arun(self, tctx: ToolContext | None = None) -> str:
        if tctx:
            tctx.raise_if_cancelled()

        path = _validate_path(self.path, tctx.cwd if tctx else None)
        query = _Query(self.pattern, self.file_pattern, self.use_ignore)
        re.compile(query.pattern)  # 语法错误在分叉前统一报出，两条分支的报错因此一致

        if not has_ripgrep():
            matches = await asyncio.to_thread(_search_python, path, query)
            return _format_matches(matches)

        matches, warning = await _search_ripgrep(tctx, path, query)
        output = _format_matches(matches)
        return f"{output}\n{warning}" if warning else output


def _search_python(root: Path, query: _Query) -> list[_Match]:
    """回退分支：枚举候选文件后逐文件流式匹配。"""
    regex = re.compile(query.pattern)
    paths = (root,) if root.is_file() else iter_files(root, query.use_ignore)
    matches: list[_Match] = []

    for path in paths:
        # 显式传入的文件也按 `file_pattern` 过滤，与 rg 分支的记录过滤保持一致
        if not path.match(query.file_pattern):
            continue
        matches.extend(_search_file(path, regex))

    return matches


def _search_file(path: Path, regex: re.Pattern[str]) -> list[_Match]:
    """逐行匹配：`\\r` 留在行内（`$` 不匹配 CRLF），遇到含 NUL 的行就把整个文件当二进制丢掉。

    编码由文件头 2 字节决定：UTF-16 BOM 走 `utf-16`（rg 也这么转码），其余 `utf-8-sig` 剥文件开头 BOM。"""
    matches: list[_Match] = []
    file_path = str(path)

    try:
        with open(path, "rb") as raw:
            encoding = "utf-16" if raw.read(2) in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE) else "utf-8-sig"
            raw.seek(0)
            with io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline="\n") as file:
                for line_no, line in enumerate(file, 1):
                    line = line.removesuffix("\n")
                    if "\0" in line:
                        return []  # rg 对含 NUL 的文件只报 "binary file matches"，不出记录
                    if regex.search(line):
                        matches.append(_Match(file_path, line_no, _normalize_line(line, line_no)))
    except OSError:
        return []  # 读不了整个文件：与 rg 跳过不可读文件一致，不吐残缺结果

    return matches


def _normalize_line(content: str, line_no: int) -> str:
    """两引擎共用：去掉行尾 CR（保持单行记录），BOM 只在第 1 行去掉。"""
    content = content.rstrip("\r")
    return content.removeprefix("\ufeff") if line_no == 1 else content


async def _search_ripgrep(tctx: ToolContext | None, root: Path,
                          query: _Query) -> tuple[list[_Match], str | None]:
    """rg 分支：不传 `--glob`（会盖掉 ignore / hidden 规则）；`use_ignore=false` 放开隐藏与忽略（同 glob）。"""
    command = ["--no-heading", "-n", "-H"]
    if not query.use_ignore:
        command += ["--no-ignore", "--hidden"]
    command += ["-e", query.pattern, "--", str(root)]

    stdout, stderr, returncode = await run_rg(command, tctx)
    error = _rg_error(returncode, stderr)
    if error and "regex parse error" in error:
        # Python `re` 合法的 lookaround / 反向引用 rg 不支持：换引擎，不静默丢结果
        return await asyncio.to_thread(_search_python, root, query), None

    matches = [match for match in _parse_match(stdout.split("\n"))
               if _keep_match(match, root, query)]
    return matches, f"(Search incomplete: {error})" if error else None


def _keep_match(match: _Match, root: Path, query: _Query) -> bool:
    """记录过滤：先 `file_pattern`；`use_ignore` 时还要求相对路径里没有隐藏片段——rg 会搜
    `.gitignore` 白名单放出来的隐藏文件（`--hidden` 的文档行为），回退分支却始终不枚举它们。"""
    if not Path(match.path).match(query.file_pattern):
        return False
    return not (query.use_ignore and is_hidden(relpath(root, match.path)))


def _rg_error(returncode: int, stderr: str) -> str | None:
    """退出码 2 = 部分路径不可读 / 语法不被 rg 接受：返回原因，交由调用方降级成警告。"""
    if returncode in (0, 1):
        return None
    return next((line.strip() for line in stderr.splitlines() if line.strip()), "unknown error")


def _validate_path(path: str = "", cwd: Path | None = None) -> Path:
    base = (cwd or WORKDIR).resolve()
    path = (base / Path(path)).resolve()  # 绝对路径也解析：`..` / 符号链接不得绕过下面的边界检查

    if not path.exists():
        raise Exception(f"path '{path}' does not exist.")

    if not path.is_relative_to(base):
        raise Exception(f"path '{path}' escapes work directory '{base}'.")

    return path


def _parse_match(lines: list[str]) -> list[_Match]:
    """`path:line:content` → `_Match`；内容规范化交给 `_normalize_line`。"""
    matches = []
    for line in lines:
        m = _MATCH_RE.match(line)
        if m:
            file_path, line_no, content = m.groups()
            line_number = int(line_no)
            matches.append(_Match(
                str(Path(file_path)), line_number, _normalize_line(content, line_number)))
    return matches


def _format_matches(matches: list[_Match]) -> str:
    """`path:line:content` 单行记录，按路径 / 行号排序；无命中回占位符，超限时末行提示。"""
    if not matches:
        return "(No matches)"

    ordered = sorted(matches, key=lambda match: (match.path, match.line_no))
    lines = [f"{match.path}:{match.line_no}:{match.content}" for match in ordered[:_MATCH_LIMIT]]
    if len(ordered) > _MATCH_LIMIT:
        lines.append(f"Results truncated. More than {_MATCH_LIMIT} matches found. "
                     "Consider a more specific path or pattern if needed.")
    return "\n".join(lines)
