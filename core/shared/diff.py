import difflib
from pathlib import Path
from typing import Optional

from core.config import WORKDIR
from core.shared.file_io import normalize_newlines

DIFF_TOOLS = ("write_file", "edit_file")


def _diff_rows(old: str, new: str, context: int = 3) -> list[tuple[str, int, str]]:
    """变化的行（含上下最多 context 行）。

    Args:
        old: 旧内容。
        new: 新内容。
        context: 变化行前后保留的上下文行数。

    Returns:
        (kind, 行号, 行) 列表；kind 为 ' ' 上下文 / '-' 删除 / '+' 新增，'-' 用旧行号，其余用新行号。
    """
    if len(old) + len(new) > 200_000:
        return []
    if old == new:
        return []
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    rows = []
    for group in difflib.SequenceMatcher(None, old_lines, new_lines).get_grouped_opcodes(context):
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for n, line in enumerate(new_lines[j1:j2], j1 + 1):
                    rows.append((" ", n, line))
            else:
                for n, line in enumerate(old_lines[i1:i2], i1 + 1):
                    rows.append(("-", n, line))
                for n, line in enumerate(new_lines[j1:j2], j1 + 1):
                    rows.append(("+", n, line))
    return rows


def resolve_path(path: str, cwd: Optional[Path] = None) -> Path:
    """相对路径按 cwd（缺省 WORKDIR）解析为绝对路径。

    Args:
        path: 原始路径。
        cwd: 基准目录。

    Returns:
        解析后的绝对路径。
    """
    return ((cwd or WORKDIR) / path).resolve()


def _read_old(fp: Path) -> str:
    """读取旧文件内容；文件不存在时按空内容处理。

    Args:
        fp: 文件路径。

    Returns:
        文件原文；不存在时为空字符串。
    """
    try:
        return fp.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def preview_write(path: str, content: str, cwd: Optional[Path] = None) -> list[tuple[str, int, str]]:
    """执行前计算 write 将产生的变化行。

    Args:
        path: 目标文件路径。
        content: 新内容。
        cwd: 基准目录。

    Returns:
        变化行列表；内容未变（按归一化换行比较）时为空列表。
    """
    return _diff_rows(_read_old(resolve_path(path, cwd)), normalize_newlines(content))


def preview_edit(
    path: str,
    old_text: str,
    new_text: str,
    cwd: Optional[Path] = None,
) -> list[tuple[str, int, str]]:
    """执行前计算 edit 将产生的变化行。

    Args:
        path: 目标文件路径。
        old_text: 要替换的原文。
        new_text: 替换后的文本。
        cwd: 基准目录。

    Returns:
        变化行列表；old_text 不存在时为空列表。
    """
    text = _read_old(resolve_path(path, cwd))
    if old_text not in text:
        return []
    return _diff_rows(text, text.replace(old_text, new_text, 1))
