# 执行前渲染 diff 预览的工具；preview 函数见 preview_write / preview_edit
import difflib
from pathlib import Path
from typing import Optional

from src.config import WORKDIR

# 执行前渲染 diff 预览的工具；preview 函数见 preview_write / preview_edit
DIFF_TOOLS = ("write_file", "edit_file")


def _diff_rows(old: str, new: str, context: int = 3) -> list[tuple[str, int, str]]:
    """变化的行（含上下最多 context 行），返回 (kind, 行号, 行)；
    kind ' ' 上下文 / '-' 删除 / '+' 新增，'-' 旧行号，其余新行号"""
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


def _resolve(path: str, cwd: Optional[Path]) -> Path:
    return ((cwd or WORKDIR) / path).resolve()


def _read_old(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def preview_write(path: str, content: str, cwd: Optional[Path] = None) -> list[tuple[str, int, str]]:
    """执行前计算 write 将产生的变化行；内容未变时返回空列表"""
    return _diff_rows(_read_old(_resolve(path, cwd)), content)


def preview_edit(path: str, old_text: str, new_text: str, cwd: Optional[Path] = None) -> list[tuple[str, int, str]]:
    """执行前计算 edit 将产生的变化行；old_text 不存在时返回空列表"""
    text = _read_old(_resolve(path, cwd))
    if old_text not in text:
        return []
    return _diff_rows(text, text.replace(old_text, new_text, 1))
