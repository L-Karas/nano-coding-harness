"""
Base tools
"""
import ast
import difflib
import json
import pathlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from core import task
from core.config import WORKDIR


def run_bash(command: str, cwd: Optional[Path] = None, run_in_background: bool = False) -> str:
    """
    run_in_background is consumed by the dispatcher; direct execution ignores it.
    """
    try:
        res = subprocess.run(command, shell=True, capture_output=True, cwd=cwd or WORKDIR, text=True, timeout=120)
        output = (res.stdout + res.stderr).strip()
        return output[:int(5e4)] if output else "(Tool no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout (120s)"


def run_read(path: str, limit: Optional[int] = None, offset: Optional[int] = 0, cwd: Optional[Path] = None) -> str:
    try:
        base = cwd or WORKDIR
        fp = (base / path).resolve()
        lines = fp.read_text(encoding="utf-8").splitlines()
        offset = max(int(offset or 0), 0)
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit}) more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


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


def run_write(path: str, content: str, cwd: Optional[Path] = None) -> str:
    try:
        fp = _resolve(path, cwd)
        if fp.exists() and _read_old(fp) == content:
            return f"No changes to {path}."
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}."
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str, cwd: Optional[Path] = None) -> str:
    try:
        fp = _resolve(path, cwd)
        text = _read_old(fp)
        if old_text not in text:
            return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return "Edited successfully."
    except Exception as e:
        return f"Error: {e}"


def run_grep(pattern: str, path: str = "", file_pattern: str = "*", cwd: Optional[Path] = None) -> str:
    base = cwd or WORKDIR
    path = pathlib.Path(path)

    try:
        regex = re.compile(pattern)
    except Exception as e:
        return f"Error: {e}"

    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        return f"Error: path '{path}' does not exist."
    if not path.is_dir():
        path = path.parent

    if not path.is_relative_to(base):
        return f"Error: path '{path}' escapes work directory '{base}'."

    iterator = path.rglob(file_pattern)
    output = []
    for file_path in iterator:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if regex.search(line):
                        output.append(f"file path: \"{file_path}\", line: [{line_no}], content: \"{line}\"")
        except Exception:
            continue

    if len(output) > 50:
        output = output[:50] + ["Results truncated. More than 50 matches found. "
                                "Consider a more specific path or pattern if needed."]

    return "\n".join(output) if output else "(No matches found)"


def run_glob(pattern: str, cwd: Optional[Path] = None) -> str:
    import glob as g
    try:
        base = cwd or WORKDIR
        results = []
        for match in g.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)

        return "\n".join(results) if results else "(No matches)"
    except Exception as e:
        return f"Error: {e}"


def call_tool_handler(handler, args: dict, name: str) -> str:
    if not handler:
        return f"Unknown: {name}"
    try:
        return handler(**args)
    except Exception as e:
        return f"Error: {e}"


def _normalize_todos(todos):
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            # json.loads 仅 JSON 格式（只支持双引号、不支持元组/集合/None等）
            try:
                # ast.literal_eval 支持 Python 字面量（支持元组、集合、None、布尔等）
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"

    if not isinstance(todos, list):
        return None, "Error: todos must be a list"

    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"Error: todos[{i}] must contain 'content' or 'status'"
        if todo["status"] not in ["pending", "in_progress", "completed"]:
            return None, f"Error: todos[{i}] has invalid status '{todo['status']}'"

    return todos, None


def run_todo_write(todos: list) -> str:
    todos, error = _normalize_todos(todos)
    if error:
        return error
    task.CURRENT_TODOS = todos
    print(f"  \033[33m[Todo Update] updated {len(task.CURRENT_TODOS)} item(s)\033[0m")
    return f"Updated {len(task.CURRENT_TODOS)} todos"


if __name__ == '__main__':
    print(run_grep("print", file_pattern="text_*", cwd=r"E:\AI-Programs\nano-harness"))
