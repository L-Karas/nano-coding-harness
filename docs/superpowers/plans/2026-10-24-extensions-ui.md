# /extensions 扩展列表弹窗实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/extensions` 弹窗，按 `/skills` 的列表样式展示全部外部扩展（含未启用）与内置扩展，并标注 `[user]` / `[builtin]` 来源。

**Architecture:** 新增进程级 `core/extension/registry.py` 登记扫描 / 注册结果；loader 扫描时登记 user 扩展（含未启用原因），builtin 注册时登记 builtin 扩展；`/extensions` 只读 registry，复用 `_ListPickerScreen` 渲染，不重新解析清单、不触碰派发语义。

**Tech Stack:** Python 3.13、Textual、Rich、pytest。

**Spec:** `docs/superpowers/specs/2026-10-24-extensions-ui-design.md`

## Global Constraints

- `requires-python >= 3.13`；不新增第三方依赖。
- 所有任务用 `uv run pytest ... -v` 验证；全量验证用 `uv run pytest tests/ -q` 与
  `uv run python -m core.tui.ui_textual --smoke`。
- 只 `git add` 当前任务 Files 列表中的文件；禁止 `git add -A`（工作树可能有其它改动）。
- 用户可见文案逐字固定：
  - 窗标题 `Extensions`、提示行 `  ↑/↓ browse    Esc close`、搜索占位 `Search extensions…`、
    列表 id `extensions-list`、指令说明 `List loaded and disabled extensions`；
  - 来源 tag 仅 `user` / `builtin`；第二行已启用为 `hooks: <事件列表>`，无事件为 `no hooks`；
  - 未启用原因：`disabled`、`missing extension.json`、`invalid extension.json`（含非对象清单）、
    `missing register(api)`、`load failed`；
  - 空列表提示 `No extensions found`，通知标题 `⚠️ Extensions`。
- 扫描规则不变：只扫 `.harness/extensions/` 直接子目录，跳过非目录和 `_` 前缀。
- registry 以 `(origin, name)` 为键：同名同源重复 `record` 原地替换，不同来源同名共存。
- `reset_extensions()` 必须同时清空 registry；不改变 dispatcher / 加载语义。
- 弹窗只读：`Enter` / 单击不关窗、不触发动作；`Esc` 关闭。

## Review Focus

- **未启用扩展不能消失或伪装成已启用**：`enabled` 非 strictly true、缺清单、JSON 非法、
  缺 `register`、注册抛错都要出现在列表里且原因正确。测试在 Task 2。
- **重复注册 / 重复调用不能出现重复行**：loader 幂等，builtin 重复注册原地替换。测试在
  Task 1、Task 2、Task 3。
- **同名 user 与 builtin 不能互相覆盖**：用户建一个 `observers` 目录时，两个来源都要在。
  测试在 Task 1。
- **边框行为**：搜索栏过滤名称，`Enter` 后弹窗仍在，`Esc` 才关闭；单击行等同 `Enter`
  且无动作。测试在 Task 4。
- **空 registry 与指令补全**：registry 为空时给提示卡而非空弹窗；`/ext` 补全到
  `/extensions` 并能打开。测试在 Task 4、Task 5。

---

### Task 1: 扩展登记表

**Files:**
- Create: `core/extension/registry.py`
- Modify: `core/extension/__init__.py`
- Test: `tests/test_extension_registry.py`
- Commit also: `docs/superpowers/specs/2026-10-24-extensions-ui-design.md`

**Interfaces:**
- Produces:
  - `Origin = Literal["user", "builtin"]`
  - `ExtensionInfo(name: str, origin: Origin, enabled: bool, detail: str = "", events: tuple[str, ...] = ())`（frozen dataclass）
  - `record(name: str, origin: Origin, *, enabled: bool, detail: str = "", events: Iterable[str] = ()) -> None`
  - `list_extensions() -> list[ExtensionInfo]`（builtin 在前，再按 `name.casefold()`）
  - `reset() -> None`
  - `core.extension` 包导出 `ExtensionInfo`、`list_extensions`；`reset_extensions()` 调用 registry reset

- [ ] **Step 1: 写失败测试**

Create `tests/test_extension_registry.py`:

```python
"""扩展登记表：来源排序、事件去重、同名同源替换、reset 清理。"""
import pytest

import core.extension.registry as registry
from core.extension import ExtensionInfo, list_extensions, reset_extensions


@pytest.fixture(autouse=True)
def _clean():
    registry.reset()
    yield
    registry.reset()


def _record_builtins_and_users():
    registry.record("zeta", "user", enabled=True, events=["before_llm"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="disabled")
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])


def test_list_puts_builtins_first_then_users_by_name():
    _record_builtins_and_users()

    assert list_extensions() == [
        ExtensionInfo("observers", "builtin", True, "", ("before_tool",)),
        ExtensionInfo("permission", "builtin", True, "", ("before_tool",)),
        ExtensionInfo("alpha", "user", False, "disabled", ()),
        ExtensionInfo("zeta", "user", True, "", ("before_llm",)),
    ]


def test_events_are_deduplicated_in_registration_order():
    registry.record("demo", "user", enabled=True,
                    events=["after_tool", "before_llm", "after_tool"])

    assert list_extensions()[0].events == ("after_tool", "before_llm")


def test_same_origin_and_name_replaces_in_place():
    registry.record("demo", "user", enabled=False, detail="disabled")
    registry.record("demo", "user", enabled=True, events=["before_llm"])

    assert list_extensions() == [
        ExtensionInfo("demo", "user", True, "", ("before_llm",)),
    ]


def test_same_name_keeps_user_and_builtin_separate():
    registry.record("permission", "user", enabled=True)
    registry.record("permission", "builtin", enabled=True)
    registry.record("permission", "user", enabled=False, detail="load failed")

    assert [(e.origin, e.enabled, e.detail) for e in list_extensions()] == [
        ("builtin", True, ""),
        ("user", False, "load failed"),
    ]


def test_reset_clears_all_entries():
    registry.record("demo", "user", enabled=True)
    reset_extensions()

    assert list_extensions() == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_extension_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.extension.registry'`

- [ ] **Step 3: 实现 registry 与导出**

Create `core/extension/registry.py`：frozen dataclass + 模块级 `list[ExtensionInfo]`；
`record()` 用 `(origin, name)` 查找，命中则原地替换、否则追加；事件按首次出现顺序去重后存
tuple；`list_extensions()` 返回按 `(origin != "builtin", name.casefold())` 排序的新列表；
`reset()` 清空。

Modify `core/extension/__init__.py`：导入 `ExtensionInfo`、`list_extensions` 与
`registry`，加入 `__all__`，并让 `reset_extensions()` 在 reset dispatcher / loader 之外
调用 `registry.reset()`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_extension_registry.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add core/extension/registry.py core/extension/__init__.py tests/test_extension_registry.py docs/superpowers/specs/2026-10-24-extensions-ui-design.md
git commit -m "feat: add extension registry for /extensions"
```

---

### Task 2: loader 登记 user 扩展（含未启用原因）

**Files:**
- Modify: `core/extension/loader.py`
- Test: `tests/test_extension_loader.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 的 `registry.record(name, origin, *, enabled, detail="", events=())`
- Produces: `load_extensions(directory: Path | None = None) -> None` 行为不变；内部改为
  `_manifest_reason(folder: Path) -> str | None` 与 `_load_extension(folder: Path) -> list[str] | None`
  （成功返回本次提交的事件列表；缺 `register` 返回 `None`；异常上抛给 `load_extensions`）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_extension_loader.py` 末尾追加（顶部补 `from core.extension import ExtensionInfo, list_extensions`）：

```python
def test_enabled_extension_recorded_with_events(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("demo", "user", True, "", ("before_llm",)),
    ]


def test_disabled_extension_recorded_with_reason(tmp_path):
    _make_extension(tmp_path, "off", REGISTER_LLM, enabled=False)
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("off", "user", False, "disabled", ()),
    ]


def test_missing_and_invalid_manifest_recorded(tmp_path):
    _make_extension(tmp_path, "missing", REGISTER_LLM, manifest=False)
    bad = _make_extension(tmp_path, "bad", REGISTER_LLM)
    (bad / "extension.json").write_text("{not json", encoding="utf-8")
    not_object = _make_extension(tmp_path, "not_object", REGISTER_LLM)
    (not_object / "extension.json").write_text("[]", encoding="utf-8")
    load_extensions(tmp_path)

    assert [(e.name, e.enabled, e.detail) for e in list_extensions()] == [
        ("bad", False, "invalid extension.json"),
        ("missing", False, "missing extension.json"),
        ("not_object", False, "invalid extension.json"),
    ]


def test_missing_register_recorded(tmp_path):
    _make_extension(tmp_path, "no_register", "X = 1\n")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("no_register", "user", False, "missing register(api)", ()),
    ]


def test_register_failure_recorded_as_load_failed(tmp_path):
    _make_extension(tmp_path, "half",
                    "def register(api):\n    api.on('before_llm', lambda ctx: None)\n    raise RuntimeError('late')\n")
    load_extensions(tmp_path)

    assert list_extensions() == [
        ExtensionInfo("half", "user", False, "load failed", ()),
    ]


def test_underscore_folder_not_recorded(tmp_path):
    _make_extension(tmp_path, "_hidden", REGISTER_LLM)
    load_extensions(tmp_path)

    assert list_extensions() == []


def test_load_twice_records_once(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    load_extensions(tmp_path)

    assert [e.name for e in list_extensions()] == ["demo"]


def test_events_keep_registration_order_and_dedupe(tmp_path):
    _make_extension(tmp_path, "multi",
                    "def register(api):\n"
                    "    api.on('after_tool', lambda ctx: None)\n"
                    "    api.on('before_llm', lambda ctx: None)\n"
                    "    api.on('after_tool', lambda ctx: None)\n")
    load_extensions(tmp_path)

    assert list_extensions()[0].events == ("after_tool", "before_llm")
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_loader.py -v`
Expected: 新增用例 FAIL（`list_extensions()` 为空）；原有用例仍 PASS

- [ ] **Step 3: 重构 loader**

`core/extension/loader.py`：

- 把 `_read_enabled` 改为 `_manifest_reason`：缺清单 / JSON 解析失败 / 非 dict →
  `invalid extension.json`；`enabled is not True` → `disabled`；通过返回 `None`。日志级别
  与文案保持现有行为。
- `load_extensions` 对每个直接子目录（跳过非目录与 `_` 前缀）：
  - 有 reason → `registry.record(name, "user", enabled=False, detail=reason)`；
  - 无 reason → `try: events = _load_extension(folder)`；
    异常 → 记录 `load failed`（日志仍为 `[Extension] 加载失败，已隔离: <name>`）；
    `events is None` → 记录 `missing register(api)`；否则记录 `enabled=True, events=events`。
- `_load_extension` 保留现有导入 / 原子注册逻辑，成功时记录原日志并返回
  `[event for event, _callback, _stage, _fail_closed in registered]`；缺 `register` 记录原日志
  并返回 `None`；任何异常先 `sys.modules.pop(module_name, None)` 再上抛。

- [ ] **Step 4: 运行 loader 测试**

Run: `uv run pytest tests/test_extension_loader.py -v`
Expected: PASS（原有用例 + 8 个新增用例）

- [ ] **Step 5: 提交**

```bash
git add core/extension/loader.py tests/test_extension_loader.py
git commit -m "feat: record user extensions and load status"
```

---

### Task 3: builtin 扩展登记

**Files:**
- Modify: `core/extension/builtin/__init__.py`
- Test: `tests/test_extension_builtin.py`（追加用例并调整 fixture）

**Interfaces:**
- Consumes: Task 1 的 `registry.record(...)`
- Produces: `register_builtin_extensions(*, rules: Rules | None = None) -> None` 行为不变；
  成功后 registry 中存在 `ExtensionInfo("observers", "builtin", True, "", ("before_tool",))`
  与 `ExtensionInfo("permission", "builtin", True, "", ("before_tool",))`

- [ ] **Step 1: 调整 fixture 并追加失败测试**

`tests/test_extension_builtin.py` 顶部补：
`from core.extension import ExtensionInfo, list_extensions, reset_extensions`
把 autouse fixture 改为调用 `reset_extensions()`（同时清 registry），再追加：

```python
def test_builtins_are_recorded_with_origin_and_events():
    register_builtin_extensions(rules=Rules())

    assert list_extensions() == [
        ExtensionInfo("observers", "builtin", True, "", ("before_tool",)),
        ExtensionInfo("permission", "builtin", True, "", ("before_tool",)),
    ]


def test_reregistering_builtins_does_not_duplicate_rows():
    register_builtin_extensions(rules=Rules())
    register_builtin_extensions(rules=Rules())

    assert [e.name for e in list_extensions()] == ["observers", "permission"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_builtin.py -v`
Expected: 新增用例 FAIL（registry 为空）

- [ ] **Step 3: 实现 builtin 登记**

`core/extension/builtin/__init__.py`：导入 `core.extension.registry as registry`；
`_register` 在 `api.commit()` 并逐个 `dispatcher.add` 之后调用
`registry.record(module_name.removeprefix("builtin:"), "builtin", enabled=True,
events=[event for event, _callback, _stage, _fail_closed in registered])`。
注册失败保持现有行为（异常上抛，不登记）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_builtin.py -v`
Expected: PASS（原有 5 个 + 新增 2 个）

- [ ] **Step 5: 提交**

```bash
git add core/extension/builtin/__init__.py tests/test_extension_builtin.py
git commit -m "feat: record builtin extensions"
```

---

### Task 4: `/extensions` 弹窗与指令接线

**Files:**
- Create: `core/tui/screens/extensions.py`
- Modify: `core/tui/screens/__init__.py`
- Modify: `core/tui/commands.py`
- Modify: `core/tui/utils.py`
- Modify: `smoke/chat.py`（精确候选表断言）
- Test: `tests/test_ui_extensions.py`

**Interfaces:**
- Consumes: Task 1 的 `ExtensionInfo`、`list_extensions()`
- Produces:
  - `ExtensionsScreen(extensions: list[ExtensionInfo])`，类属性
    `TITLE = "Extensions"`、`HINT = "  ↑/↓ browse    Esc close"`、
    `LIST_ID = "extensions-list"`、`SEARCH_PLACEHOLDER = "Search extensions…"`
  - `ChatApp._open_extensions() -> None`
  - `_OPENERS["/extensions"] = "_open_extensions"`；`SLASH_COMMANDS` 含 `/extensions`

- [ ] **Step 1: 写失败测试**

Create `tests/test_ui_extensions.py`:

```python
"""`/extensions` 弹窗：来源 tag、hook / 未启用原因、搜索、Enter 无动作、Esc 关闭、空列表提示。"""
import asyncio

import pytest
from textual.widgets import Input, OptionList

import core.extension.registry as registry
import core.tui.render as _render
from core.extension import list_extensions
from core.tui.screens import ExtensionsScreen
from core.tui.ui_textual import ChatApp
from core.tui.utils import SLASH_COMMANDS


@pytest.fixture(autouse=True)
def _extensions():
    original = list_extensions()
    registry.reset()
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="invalid extension.json")
    registry.record("zeta", "user", enabled=True, events=["before_llm", "after_tool"])
    yield
    registry.reset()
    for entry in original:
        registry.record(entry.name, entry.origin, enabled=entry.enabled,
                        detail=entry.detail, events=entry.events)


def _rows(app):
    screen = app.screen_stack[-1]
    lst = screen.query_one("#extensions-list", OptionList)
    return screen, lst, [lst.get_option_at_index(i).prompt.plain for i in range(lst.option_count)]


def test_extensions_command_is_completable_and_opens_read_only_screen():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            assert "/extensions" in SLASH_COMMANDS
            prompt = app.query_one("#prompt")
            prompt.focus()
            prompt.text = "/extensions"
            await pilot.press("enter")
            await pilot.pause()
            assert app._exception is None
            screen, lst, rows = _rows(app)
            assert isinstance(screen, ExtensionsScreen)
            assert rows == [
                "● observers [builtin]\nhooks: before_tool",
                "● permission [builtin]\nhooks: before_tool",
                "● alpha [user]\ninvalid extension.json",
                "● zeta [user]\nhooks: before_llm, after_tool",
            ]
            assert screen.TITLE == "Extensions"
            assert screen.HINT == "  ↑/↓ browse    Esc close"
            search = screen.query_one("#search-input", Input)
            assert search.placeholder == "Search extensions…"
            assert search.has_focus
    asyncio.run(main())


def test_search_filters_by_name_and_enter_does_not_close():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            app._open_extensions()
            await pilot.pause()
            screen, lst, _rows_before = _rows(app)
            search = screen.query_one("#search-input", Input)
            search.value = "ZETA"
            await pilot.pause()
            assert [lst.get_option_at_index(i).prompt.plain for i in range(lst.option_count)] == [
                "● zeta [user]\nhooks: before_llm, after_tool",
            ]
            search.value = ""
            await pilot.pause()
            assert lst.option_count == 4
            await pilot.click("#extensions-list", offset=(2, 0))
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ExtensionsScreen), "单击行不应关窗"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ExtensionsScreen), "Enter 不应关窗"
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ExtensionsScreen)
    asyncio.run(main())


def test_enabled_extension_without_events_shows_no_hooks():
    async def main():
        registry.record("silent", "user", enabled=True)
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            app._open_extensions()
            await pilot.pause()
            _screen, _lst, rows = _rows(app)
            assert "● silent [user]\nno hooks" in rows
    asyncio.run(main())


def test_empty_registry_notifies_instead_of_opening():
    async def main():
        registry.reset()
        app = ChatApp(handle_query=lambda query: None)
        _render._APP = app
        try:
            async with app.run_test() as pilot:
                app._open_extensions()
                await pilot.pause()
                assert not isinstance(app.screen_stack[-1], ExtensionsScreen)
                bodies = [str(w.render()) for w in app.query(".card-body")]
                assert any("No extensions found" in body for body in bodies), bodies
        finally:
            _render._APP = None
    asyncio.run(main())
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_ui_extensions.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExtensionsScreen'`

- [ ] **Step 3: 实现屏幕与接线**

Create `core/tui/screens/extensions.py`：

```python
"""扩展列表弹窗（/extensions）：只读展示全部扩展与来源标签。"""

from __future__ import annotations

from textual.widgets.option_list import Option

from core.extension import ExtensionInfo
from core.tui.screens.base import _entry_row, _ListPickerScreen, _rebuild_options


def _summary(entry: ExtensionInfo) -> str:
    if not entry.enabled:
        return entry.detail or "disabled"
    return f"hooks: {', '.join(entry.events)}" if entry.events else "no hooks"


class ExtensionsScreen(_ListPickerScreen):
    TITLE = "Extensions"
    HINT = "  ↑/↓ browse    Esc close"
    LIST_ID = "extensions-list"
    SEARCH_PLACEHOLDER = "Search extensions…"

    def __init__(self, extensions: list[ExtensionInfo]) -> None:
        super().__init__()
        self._extensions = extensions

    def _reload(self) -> None:
        _rebuild_options(
            self._list(),
            [Option(_entry_row(entry.name, _summary(entry), entry.origin),
                    id=f"{entry.origin}:{entry.name}")
             for entry in self._extensions if self._match(entry.name)],
        )
```

`core/tui/screens/__init__.py` 导入并导出 `ExtensionsScreen`。
`core/tui/utils.py` 的 `_SLASH_COMMAND_META` 在 `/skills` 后加
`("/extensions", (), "List loaded and disabled extensions")`。
`smoke/chat.py` 的完整候选断言同步插入 `/extensions`（候选列表与候选行表；行内容为
`/extensions` + 8 个空格 + `List loaded and disabled extensions`，说明列宽仍由
`/login (logout)` 决定）。
`core/tui/commands.py`：导入 `ExtensionsScreen` 与 `core.extension.list_extensions`；
`_OPENERS` 加 `"/extensions": "_open_extensions"`；实现：

```python
def _open_extensions(self) -> None:
    """/extensions：全部扩展（含未启用）与来源标签；只读，Esc 关闭。"""
    rows = list_extensions()
    if not rows:
        render_background_notification("No extensions found", title="⚠️ Extensions")
        return
    self.push_screen(ExtensionsScreen(rows))
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_ui_extensions.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add core/tui/screens/extensions.py core/tui/screens/__init__.py core/tui/commands.py core/tui/utils.py smoke/chat.py tests/test_ui_extensions.py
git commit -m "feat: add /extensions popup"
```

---

### Task 5: 冒烟、文档与全量验证

**Files:**
- Create: `smoke/extensions.py`
- Modify: `smoke/__init__.py`
- Modify: `README.md`
- Modify: `docs/extensions.md`
- Commit also: `docs/superpowers/plans/2026-10-24-extensions-ui.md`

**Interfaces:**
- Consumes: Task 4 的 `ExtensionsScreen` 与 `/extensions` 指令
- Produces: `smoke.extensions.run(app, pilot)`，挂入 `_SECTIONS`

- [ ] **Step 1: 写冒烟段**

Create `smoke/extensions.py`：快照 `list_extensions()`，`registry.reset()` 后登记一条
builtin、一条 enabled user、一条 disabled user（原因 `missing extension.json`）；先
`prompt.focus()`，用 `type_query(prompt, pilot, "/ext")` 断言候选为 `["/extensions"]`，
Enter 打开后断言三行文本与搜索过滤、单击 / `Enter` 不关窗、`Esc` 关窗；随后
`registry.reset()` 并打开一次 `/extensions`，
断言出现 `No extensions found` 提示卡；`finally` 恢复快照。行断言：

```python
["● smoke-builtin [builtin]\nhooks: before_tool",
 "● smoke-off [user]\nmissing extension.json",
 "● smoke-on [user]\nhooks: before_llm, after_tool"]
```

`smoke/__init__.py` 导入 `extensions` 并插入 `_SECTIONS`（放在 `skills` 后、`mcp` 前）。

- [ ] **Step 2: 运行冒烟**

Run: `uv run python -m core.tui.ui_textual --smoke`
Expected: 通过，输出含 `/extensions` 相关断言

- [ ] **Step 3: 补文档**

`README.md` 内置指令列表在 `/skills` 后加：
`` `/extensions`（扩展列表，含未启用项；`[user]` / `[builtin]` 标注来源） ``。
`docs/extensions.md` 在「1. 快速开始」末尾加一小节「1.4 TUI 查看扩展」：说明 `/extensions`
列出已启用与未启用扩展、来源 tag、`hooks:` / 未启用原因，且加载状态仍需重启进程才刷新。

- [ ] **Step 4: 全量验证**

Run: `uv run pytest tests/ -q`
Expected: 全绿（0 failed）

Run: `uv run python -m core.tui.ui_textual --smoke`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add smoke/extensions.py smoke/__init__.py README.md docs/extensions.md docs/superpowers/plans/2026-10-24-extensions-ui.md
git commit -m "docs: document /extensions and add smoke coverage"
```
