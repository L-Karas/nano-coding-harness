"""域层零 TUI 依赖：core/（除 core/tui/）不得 import core.tui（ADR-0005）。"""
import re
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent / "core"
# 组合根：CLI 入口负责把 runtime 接到 Textual UI 上，不属于域层（ADR-0013）。
_ENTRY_MODULES = {"core/cli.py"}
_TUI_IMPORT = re.compile(
    r"^\s*(?:from\s+core\.tui(?:\.[\w.]+)?\s+import|import\s+core\.tui\b)", re.MULTILINE)


def test_domain_does_not_import_tui():
    offenders = []
    for path in _CORE.rglob("*.py"):
        if "tui" in path.relative_to(_CORE).parts:
            continue
        relative = str(path.relative_to(_CORE.parent)).replace("\\", "/")
        if relative in _ENTRY_MODULES:
            continue
        if _TUI_IMPORT.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    assert not offenders, f"域层不得 import core.tui: {offenders}"
