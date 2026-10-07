"""
Worktree System

Worktree names become filesystem paths, so the teaching version keeps the
validation rules strict and reuses them for create/remove/keep.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from core.config import WORKDIR, WORKTREES_DIR
from core.experimental.task import load_task, save_task
from core.log import get_logger

_LOGGER = get_logger(__name__)

VALID_WT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def validate_worktree_name(name: str) -> str | None:
    if not name:
        return "Worktree name cannot be empty."

    if name in (".", ".."):
        return f"'{name}' is not a valid worktree name."

    if not VALID_WT_NAME.match(name):
        return (f"'{name}' is not a valid worktree name. Only letters, digits, dots, underscores and dashes are allowed. "
                "Max length is 64.")

    return None


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    event = {"type": event_type, "worktree": worktree_name, "task_id": task_id, "timestamp": time.time()}
    event_file = WORKTREES_DIR / "events.jsonl"
    with event_file.open(mode="a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_git(args: list[str]) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = (res.stdout + res.stderr).strip()
        return res.returncode == 0, output[:5000] if output else "No tool output."
    except Exception as e:
        return False, f"git error: {e}"


def bind_task_to_worktree(task_id: str, worktree_name: str):
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)


def create_worktree(name: str, task_id: str = "") -> str:
    err = validate_worktree_name(name)
    if err:
        return err

    if task_id:
        try:
            load_task(task_id)
        except Exception as e:
            raise Exception(f"Task {task_id} does not exist. {e}")

    path = WORKTREES_DIR / name
    if path.exists():
        return f"Worktree {name} already exists at {path}."
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        raise Exception(f"git error: {result}")
    if task_id:
        bind_task_to_worktree(task_id, name)

    log_event("create", name, task_id)
    _LOGGER.info(f"[Worktree Create] created: {name} at {path}")

    return f"Worktree '{name}' created at {path}."


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    """Return (uncommitted_file_count, unpushed_commit_count) for a worktree, or (-1, -1) on failure."""
    try:
        # --porcelain gives machine-parseable output: one file per line, two-char status prefix
        uncommitted_files = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30
        )
        len_uncommitted = len([file for file in uncommitted_files.stdout.strip().splitlines() if file.strip()])
        # @{push}..HEAD: commits in local HEAD but not yet pushed to the upstream remote
        unpushed_commits = subprocess.run(
            ["git", "log", "@{push}..HEAD", "--oneline"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30
        )
        len_unpushed = len([commit for commit in unpushed_commits.stdout.strip().splitlines() if commit.strip()])
        return len_uncommitted, len_unpushed
    except Exception:
        return -1, -1


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    err = validate_worktree_name(name)
    if err:
        return err
    path = WORKTREES_DIR / name
    if not path.exists():
        raise Exception(f"Worktree {name} does not exist.")
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return "Cannot verify status. Use discard_changes=true to force discard changes."
        if files > 0 or commits > 0:
            return (f"Worktree '{name}' has {files} uncommitted files and {commits} unpushed commits. "
                    f"Use discard_changes=true to remove worktree or keep_worktree tool to keep worktree.")

    ok, result = run_git(["worktree", "remove", str(path), "--force"])
    if not ok:
        raise Exception(f"Failed to remove worktree '{name}' at {path}. {result}")

    ok, result = run_git(["branch", "-D", f"wt/{name}"])
    if not ok:
        raise Exception(f"Successfully remove worktree '{name}' at {path}. "
                        f"Failed to delete branch 'wt/{name}'. {result}")

    log_event("remove", name)
    _LOGGER.info(f"[Worktree Remove] removed worktree '{name}' at '{path}'")

    return f"Worktree '{name}' removed at '{path}'."


def keep_worktree(name: str) -> str:
    err = validate_worktree_name(name)
    if err:
        raise Exception(err)
    log_event("keep", name)
    return f"Worktree '{name}' kept for review (branch: wt/{name})."
