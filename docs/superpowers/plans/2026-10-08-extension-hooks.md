# 外部扩展 Hook 系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `.harness/extensions/<name>/` 外部扩展机制，让进程内 Python 扩展在 LLM 调用与工具调用的前/后四个节点上观察、修改与控制执行。

**Architecture:** 新增 `core/extension/` 包（context / api / dispatcher / loader）；主循环与子代理各增量插入 4 个 `dispatch` 调用；旧 `core/hook/hook.py` 完全不动，工具节点先跑外部 hook 修改参数、再跑旧权限门检查最终参数。

**Tech Stack:** Python 3.13、asyncio、importlib、dataclasses、pytest；命令 `uv run pytest tests/`。

**Spec:** `docs/superpowers/specs/2026-10-08-extension-hooks-design.md`

## Global Constraints

- 不修改 `core/hook/hook.py`；现有 `trigger_hooks` 行为不变。
- 扩展目录固定 `.harness/extensions/`，只扫描直接子文件夹；每个扩展 = `extension.json` + `__init__.py`（包式加载，可用相对导入）。
- `extension.json` 的 `enabled` 必须严格为布尔 `true`；缺失、非法 JSON、其它真值（`"true"`、`1`、`null`）一律不加载。
- hook 异常 fail-open：只 `except Exception`，不得捕获 `CancelledError`/`BaseException`。
- 工具节点顺序固定：外部 `before_tool` → 参数回写 `tool_call.function.arguments` → 旧 `trigger_hooks("pre_tool_call")` → 执行 → 外部 `after_tool`。
- 提交信息用中文；每个任务结束跑该任务测试与相关回归后提交。
- 不改 spec 之外的既有行为。

## Review Focus

1. `enabled` 写成 `"true"`（字符串）或 `1`：必须按不启用处理（Task 3 测试覆盖）。
2. 启用的扩展缺 `__init__.py` 或导入抛错：只跳过该扩展，其它扩展照常加载（Task 3 测试覆盖）。
3. hook 抛 `asyncio.CancelledError`：必须向上传播，不被 fail-open 吞掉（Task 2 测试覆盖）。
4. 重复调用 `load_extensions()`：不得重复注册（Task 3 测试覆盖）。
5. hook 把 `inject_messages` 改成非 list：丢弃该字段并记 ERROR，回合继续（Task 2 测试覆盖）。

---

### Task 1: 上下文对象与注册 API

**Files:**
- Create: `core/extension/__init__.py`
- Create: `core/extension/context.py`
- Create: `core/extension/api.py`
- Test: `tests/test_extension_api.py`

**Interfaces:**
- Consumes: 无。
- Produces（`core/extension/context.py`，普通 dataclass）：
  - `BeforeLLMContext(messages: list[dict], tools: list[dict], max_tokens: int)`，字段 `aborted: bool = False`、`abort_reason: str = ""`，方法 `abort(reason: str) -> None`
  - `AfterLLMContext(content: str | None, tool_calls: list[dict], finish_reason: str = "", usage: Any = None)`，字段 `inject_messages: list[dict]`（`default_factory=list`）
  - `BeforeToolContext(tool_name: str, args: dict)`，字段 `blocked: bool = False`、`block_reason: str = ""`，方法 `block(reason: str) -> None`
  - `AfterToolContext(tool_name: str, args: dict, result: str, is_error: bool = False)`，字段 `inject_messages: list[dict]`
- Produces（`core/extension/api.py`）：`EVENTS: tuple[str, ...]`；`ExtensionAPI(module_name: str)`：`on(event, callback) -> None`（只缓冲；未知事件抛 `ValueError`，非 callable 抛 `TypeError`）、`commit() -> list[tuple[str, Callable]]`（返回全部缓冲并清空）。

- [ ] **Step 1: 写失败测试** `tests/test_extension_api.py`

```python
"""ExtensionAPI 注册缓冲与上下文对象的状态方法。"""
import pytest

from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext,
)


def test_on_buffers_and_commit_returns_registrations():
    api = ExtensionAPI("nano_extension_demo")
    fn = lambda ctx: None
    api.on("before_llm", fn)
    assert api.commit() == [("before_llm", fn)]
    assert api.commit() == []  # 已清空


def test_on_rejects_unknown_event_and_non_callable():
    api = ExtensionAPI("nano_extension_demo")
    with pytest.raises(ValueError):
        api.on("never", lambda ctx: None)
    with pytest.raises(TypeError):
        api.on("before_llm", "not-callable")


def test_four_events_are_declared():
    assert EVENTS == ("before_llm", "after_llm", "before_tool", "after_tool")


def test_abort_and_block_helpers_capture_state():
    llm = BeforeLLMContext(messages=[], tools=[], max_tokens=10)
    llm.abort("policy")
    assert llm.aborted and llm.abort_reason == "policy"

    tool = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    tool.block("no shell")
    assert tool.blocked and tool.block_reason == "no shell"


def test_after_contexts_start_with_empty_inject_messages():
    assert AfterLLMContext(content=None, tool_calls=[]).inject_messages == []
    assert AfterToolContext(tool_name="terminal", args={}, result="out").inject_messages == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.extension.api'`）

- [ ] **Step 3: 实现**

`context.py`：四个 `@dataclass`，字段顺序按上面 Interfaces（非默认参数在前）；`abort`/`block` 用 `str(reason)` 写入原因。
`api.py`：`EVENTS` 常量；`ExtensionAPI.__init__` 保存 `module_name` 并建 `self._pending: list[tuple[str, Callable]] = []`；`on` 校验后 append；`commit` 用 `pending, self._pending = self._pending, []` 返回并清空。
`__init__.py`：导出 `EVENTS`、`ExtensionAPI` 与四个上下文类（`__all__` 列全）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_api.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add core/extension/__init__.py core/extension/context.py core/extension/api.py tests/test_extension_api.py
git commit -m "新增扩展系统上下文对象与注册 API"
```

---

### Task 2: 事件派发器

**Files:**
- Create: `core/extension/dispatcher.py`
- Test: `tests/test_extension_dispatcher.py`

**Interfaces:**
- Consumes: Task 1 的上下文类。
- Produces（`core/extension/dispatcher.py`）：
  - `add(event: str, callback: Callable, module_name: str) -> None`
  - `async def dispatch(event: str, ctx: Any) -> None`（原地修改 ctx）
  - `reset() -> None`（清空注册表，供测试）
  - 模块级 `_LOGGER`（`get_logger(__name__)`），测试可 monkeypatch。

- [ ] **Step 1: 写失败测试** `tests/test_extension_dispatcher.py`

```python
"""派发器：顺序链式、短路、异步、fail-open、CancelledError 传播、字段归一。"""
import asyncio
from unittest.mock import MagicMock

import pytest

import core.extension.dispatcher as dispatcher
from core.extension.context import AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext


@pytest.fixture(autouse=True)
def _clean_registry():
    dispatcher.reset()
    yield
    dispatcher.reset()


def _run(event, ctx):
    asyncio.run(dispatcher.dispatch(event, ctx))


def test_hooks_run_in_order_and_chain_mutations():
    seen = []

    def first(ctx):
        seen.append("first")
        ctx.args["command"] = "ls -la"

    def second(ctx):
        seen.append("second")
        assert ctx.args["command"] == "ls -la"

    dispatcher.add("before_tool", first, "ext_a")
    dispatcher.add("before_tool", second, "ext_a")
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={"command": "ls"}))
    assert seen == ["first", "second"]


def test_block_short_circuits_remaining_hooks():
    calls = []

    def later(ctx):
        calls.append("later")

    dispatcher.add("before_tool", lambda ctx: ctx.block("denied"), "ext_a")
    dispatcher.add("before_tool", later, "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.blocked and calls == []


def test_abort_short_circuits_remaining_hooks():
    calls = []

    def aborter(ctx):
        ctx.abort("stop")

    dispatcher.add("before_llm", aborter, "ext_a")
    dispatcher.add("before_llm", lambda ctx: calls.append("later"), "ext_a")
    ctx = BeforeLLMContext(messages=[], tools=[], max_tokens=1)
    _run("before_llm", ctx)
    assert ctx.aborted and calls == []


def test_async_hook_is_awaited():
    seen = []

    async def hook(ctx):
        seen.append("async")

    dispatcher.add("before_llm", hook, "ext_a")
    _run("before_llm", BeforeLLMContext(messages=[], tools=[], max_tokens=1))
    assert seen == ["async"]


def test_hook_exception_is_fail_open(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)
    calls = []

    def broken(ctx):
        raise RuntimeError("boom")

    dispatcher.add("before_tool", broken, "ext_a")
    dispatcher.add("before_tool", lambda ctx: calls.append("next"), "ext_b")
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert calls == ["next"]
    assert logger.exception.called


def test_cancelled_error_propagates():
    async def cancelled(ctx):
        raise asyncio.CancelledError

    dispatcher.add("before_tool", cancelled, "ext_a")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dispatcher.dispatch(
            "before_tool", BeforeToolContext(tool_name="terminal", args={})))


def test_non_list_inject_messages_is_dropped(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)

    dispatcher.add("after_tool", lambda ctx: setattr(ctx, "inject_messages", "oops"), "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert ctx.inject_messages == []
    assert logger.error.called


def test_result_is_normalized_and_none_content_preserved():
    dispatcher.add("after_tool", lambda ctx: setattr(ctx, "result", 42), "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert ctx.result == "42"

    dispatcher.add("after_llm", lambda ctx: setattr(ctx, "content", None), "ext_a")
    llm_ctx = AfterLLMContext(content="x", tool_calls=[])
    _run("after_llm", llm_ctx)
    assert llm_ctx.content is None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_dispatcher.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.extension.dispatcher'`）

- [ ] **Step 3: 实现 `dispatcher.py`**

```python
_HANDLERS: dict[str, list[tuple[str, Callable]]] = {}


def add(event, callback, module_name) -> None:
    _HANDLERS.setdefault(event, []).append((module_name, callback))


def reset() -> None:
    for handlers in _HANDLERS.values():
        handlers.clear()


async def dispatch(event, ctx) -> None:
    for module_name, callback in list(_HANDLERS.get(event, ())):
        try:
            result = callback(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _LOGGER.exception(
                f"[Extension] {module_name}:{getattr(callback, '__name__', callback)} failed at {event}")
            continue
        if getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False):
            break
    _normalize(ctx)
```

`_normalize(ctx)`：`hasattr(ctx, "result")` → `ctx.result = str(ctx.result)`；`hasattr(ctx, "content") and ctx.content is not None` → `str()`；`tool_calls`/`inject_messages` 存在且非 list → 记 `_LOGGER.error` 并置 `[]`。
注意：`CancelledError` 继承 `BaseException`，`except Exception` 天然不捕获。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_dispatcher.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add core/extension/dispatcher.py tests/test_extension_dispatcher.py
git commit -m "新增扩展事件派发器：链式、短路、fail-open 与字段归一"
```

---

### Task 3: 加载器、配置与启动接线

**Files:**
- Create: `core/extension/loader.py`
- Modify: `core/extension/__init__.py`（补 dispatcher / loader 导出与 `reset_extensions`）
- Modify: `core/config.py`（`SKILL_DIR` 行后加 `EXTENSION_DIR`）
- Modify: `core/bootstrap.py`（`_CONFIG_DIRS` + `load_extensions()` 调用）
- Test: `tests/test_extension_loader.py`
- Modify: `tests/test_bootstrap.py`（并发测试补 extensions 步骤 + 新断言）

**Interfaces:**
- Consumes: Task 1 `ExtensionAPI`；Task 2 `dispatcher.add` / `dispatcher.reset`。
- Produces:
  - `core.extension.loader.load_extensions(directory: Path | None = None) -> None`（默认 `EXTENSION_DIR`；进程内幂等）
  - `core.extension.loader.reset() -> None`
  - `core.extension.load_extensions`、`core.extension.reset_extensions`、`core.extension.dispatch`（`__init__` 再导出）
  - `core.config.EXTENSION_DIR = HARNESS_CONFIG_DIR / "extensions"`

- [ ] **Step 1: 写失败测试** `tests/test_extension_loader.py`

```python
"""加载器：清单门槛、原子注册、失败隔离、包内相对导入、幂等。"""
import json
from pathlib import Path

import pytest

import core.extension.dispatcher as dispatcher
from core.extension import reset_extensions
from core.extension.loader import load_extensions

REGISTER_LLM = "def register(api):\n    api.on('before_llm', lambda ctx: None)\n"


@pytest.fixture(autouse=True)
def _clean():
    reset_extensions()
    yield
    reset_extensions()


def _make_extension(root: Path, name: str, body: str = "", *, enabled=True, manifest=True, entry=True):
    folder = root / name
    folder.mkdir(parents=True)
    if manifest:
        (folder / "extension.json").write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    if entry:
        (folder / "__init__.py").write_text(body, encoding="utf-8")
    return folder


def _count(event):
    return len(dispatcher._HANDLERS.get(event, ()))


def test_enabled_extension_registers(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_not_exactly_true_is_disabled(tmp_path, value):
    _make_extension(tmp_path, "demo", REGISTER_LLM, enabled=value)
    load_extensions(tmp_path)
    assert _count("before_llm") == 0


def test_missing_or_invalid_manifest_isolated(tmp_path):
    _make_extension(tmp_path, "no_manifest", REGISTER_LLM, manifest=False)
    bad = _make_extension(tmp_path, "bad", REGISTER_LLM)
    (bad / "extension.json").write_text("{not json", encoding="utf-8")
    _make_extension(tmp_path, "good", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1  # 只有 good 生效


def test_import_error_isolated(tmp_path):
    _make_extension(tmp_path, "broken", "raise RuntimeError('import boom')\n")
    _make_extension(tmp_path, "good", "def register(api):\n    api.on('before_tool', lambda ctx: None)\n")
    load_extensions(tmp_path)
    assert _count("before_tool") == 1


def test_missing_entry_and_missing_register_skipped(tmp_path):
    _make_extension(tmp_path, "no_entry", entry=False)
    _make_extension(tmp_path, "no_register", "X = 1\n")
    load_extensions(tmp_path)
    assert not any(dispatcher._HANDLERS.values())


def test_register_failure_registers_nothing(tmp_path):
    _make_extension(tmp_path, "half",
                    "def register(api):\n    api.on('before_llm', lambda ctx: None)\n    raise RuntimeError('late')\n")
    load_extensions(tmp_path)
    assert _count("before_llm") == 0  # 原子性：不允许半个模块生效


def test_package_relative_import(tmp_path):
    folder = _make_extension(tmp_path, "pkg", "from .helper import VALUE\n\n" + REGISTER_LLM)
    (folder / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    load_extensions(tmp_path)
    assert _count("before_llm") == 1


def test_underscore_folder_skipped(tmp_path):
    _make_extension(tmp_path, "_hidden", REGISTER_LLM)
    load_extensions(tmp_path)
    assert _count("before_llm") == 0


def test_load_is_idempotent(tmp_path):
    _make_extension(tmp_path, "demo", REGISTER_LLM)
    load_extensions(tmp_path)
    load_extensions(tmp_path)
    assert _count("before_llm") == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_loader.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.extension.loader'`）

- [ ] **Step 3: 实现**

`loader.py`：
- `load_extensions(directory=None)`：`_LOADED` 全局守卫，重复调用直接返回（含显式传目录的幂等测试）；`target = directory or EXTENSION_DIR`；不存在则返回；`sorted(target.iterdir())`，跳过非目录与 `_` 开头；`_read_enabled` 通过才 `_load_extension`。
- `_read_enabled(folder)`：无 `extension.json` → INFO + False；JSON 解析失败 → ERROR + False；`not isinstance(data, dict) or data.get("enabled") is not True` → DEBUG + False。
- `_load_extension(folder)`：`module_name = f"nano_extension_{folder.name}"`；`spec_from_file_location(module_name, folder / "__init__.py", submodule_search_locations=[str(folder)])`；`module_from_spec` → `sys.modules[module_name] = module` → `exec_module`；`register` 不可调用 → INFO 返回；`register(api)` 成功后 `for event, callback in api.commit(): add(event, callback, module_name)`；任何 `except Exception` → `_LOGGER.exception(...)` + `sys.modules.pop(module_name, None)` 后返回。
- `reset()`：`_LOADED = False`。

`core/config.py`：`SKILL_DIR` 后加 `EXTENSION_DIR = HARNESS_CONFIG_DIR / "extensions"`。

`core/bootstrap.py`：顶部 `from core.config import (...)` 列表加 `EXTENSION_DIR`；`_CONFIG_DIRS` 加入 `EXTENSION_DIR`；`bootstrap()` 内加 `from core.extension.loader import load_extensions`，调用顺序 `install_default_hooks()` → `load_extensions()` → `scan_skills()`。

`core/extension/__init__.py`：补 `from .dispatcher import add, dispatch, reset as reset_dispatcher`、`from .loader import load_extensions, reset as reset_loader`；定义 `reset_extensions()` 依次调用两者；`__all__` 更新。

`tests/test_bootstrap.py`：
- 新测试 `test_extension_dir_is_bootstrapped`：`assert config.EXTENSION_DIR in bootstrap_mod._CONFIG_DIRS`。
- 并发测试：`import core.extension.loader as extension_loader`；`monkeypatch.setattr(extension_loader, "load_extensions", _record("extensions"))`；期望 `sorted(calls) == ["cron", "extensions", "harness", "hooks", "registry", "skills"]`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_loader.py tests/test_bootstrap.py tests/test_extension_api.py tests/test_extension_dispatcher.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/extension/__init__.py core/extension/loader.py core/config.py core/bootstrap.py tests/test_extension_loader.py tests/test_bootstrap.py
git commit -m "新增扩展加载器：清单门槛、原子注册与启动接线"
```

---

### Task 4: 主循环四节点接线

**Files:**
- Modify: `core/loop_with_interrupt.py`
- Test: `tests/test_extension_loop_wiring.py`

**Interfaces:**
- Consumes: Task 3 的 `core.extension`：`BeforeLLMContext`、`AfterLLMContext`、`BeforeToolContext`、`AfterToolContext`、`dispatch`。
- Produces: 主循环在四个节点触发对应事件；执行语义见 spec 5.3。

- [ ] **Step 1: 写失败测试** `tests/test_extension_loop_wiring.py`

```python
"""四个扩展事件在主循环的接线：abort/改参/拦截/结果改写与注入。"""
import contextlib

import pytest

import core.loop_with_interrupt as lwi
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook

TOOL_CALL = {"id": "t1", "type": "function",
             "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


class _Mgr:
    def __init__(self):
        self.messages = []

    def load_messages(self):
        return list(self.messages)

    def update_messages(self, messages):
        self.messages = list(messages)

    def add_message(self, message):
        self.messages.append(message)


def _prepare_runtime(monkeypatch, mgr, responses, execute):
    """responses 为 (content, reasoning, tool_calls, finish_reason, usage) 列表。"""
    calls = {"llm": [], "tools": []}

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", mgr)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: ([], {"terminal": object()}))
    monkeypatch.setattr(lwi, "should_run_background", lambda *a: False)
    monkeypatch.setattr(lwi, "trigger_hooks", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_result_diff", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_background_notification", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_working_status", contextlib.nullcontext)

    async def _prepare(messages, ctx):
        return messages

    async def _execute(handler, args, name, ctx):
        calls["tools"].append(args)
        return execute

    monkeypatch.setattr(lwi, "prepare_messages", _prepare)
    monkeypatch.setattr(lwi, "execute_tool", _execute)

    runtime = lwi.AgentRuntime()

    async def _call_llm(**kwargs):
        calls["llm"].append(kwargs)
        return object()

    async def _stream(_stream):
        return responses.pop(0)

    monkeypatch.setattr(runtime, "call_llm", _call_llm)
    monkeypatch.setattr(runtime, "stream", _stream)
    return runtime, calls


def test_before_llm_abort_ends_turn_without_model_call(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [], "unused")
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    runtime.submit(runtime.run())
    assert calls["llm"] == []
    assert mgr.messages[-1]["content"] == "[Extension aborted] policy"


def test_before_llm_mutation_reaches_model(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [("answer", "", [], "stop", None)], "unused")
    add_hook("before_llm", lambda ctx: ctx.messages.append({"role": "user", "content": "injected"}), "test")
    runtime.submit(runtime.run())
    assert any(m["content"] == "injected" for m in calls["llm"][0]["messages"])


def test_after_llm_rewrites_content_and_injects(monkeypatch):
    mgr = _Mgr()
    runtime, _ = _prepare_runtime(monkeypatch, mgr, [("original", "", [], "stop", None)], "unused")

    def hook(ctx):
        ctx.content = "rewritten"
        ctx.inject_messages.append({"role": "user", "content": "note"})

    add_hook("after_llm", hook, "test")
    runtime.submit(runtime.run())
    assert mgr.messages[-2]["content"] == "rewritten"
    assert mgr.messages[-1] == {"role": "user", "content": "note"}


def test_before_tool_block_writes_result_and_skips_execution(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "unused")
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    runtime.submit(runtime.run())
    assert calls["tools"] == []
    tool_message = next(m for m in mgr.messages if m.get("tool_call_id") == "t1")
    assert tool_message["content"] == "[Extension blocked] no shell"


def test_before_tool_args_mutation_reaches_executor_and_permission(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "raw")
    seen = []
    monkeypatch.setattr(lwi, "trigger_hooks", lambda event, tool_call: seen.append(tool_call) or None)
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")
    runtime.submit(runtime.run())
    assert calls["tools"] == [{"command": "ls -la"}]
    assert '"command": "ls -la"' in seen[0].function.arguments


def test_after_tool_result_rewrite_and_inject(monkeypatch):
    mgr = _Mgr()
    runtime, _ = _prepare_runtime(monkeypatch, mgr,
                                  [("", "", [TOOL_CALL], "tool_calls", None),
                                   ("done", "", [], "stop", None)], "raw")

    def hook(ctx):
        ctx.result = "processed"
        ctx.inject_messages.append({"role": "user", "content": "after-tool note"})

    add_hook("after_tool", hook, "test")
    runtime.submit(runtime.run())
    tool_messages = [m for m in mgr.messages if m.get("tool_call_id") == "t1"]
    assert tool_messages[0]["content"] == "processed"
    assert mgr.messages[-1] == {"role": "user", "content": "after-tool note"}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_loop_wiring.py -v`
Expected: FAIL（事件未接线：abort 测试仍调用模型等）

- [ ] **Step 3: 修改 `core/loop_with_interrupt.py`**

import 区（`from core.cron_scheduler import consume_cron_queue` 之后）加：

```python
from core.extension import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, dispatch,
)
```

`run()` 中 `try: stream = await self.call_llm(...)` 之前插入：

```python
llm_ctx = BeforeLLMContext(messages=messages, tools=tools, max_tokens=max_tokens)
await dispatch("before_llm", llm_ctx)
if llm_ctx.aborted:
    abort_text = f"[Extension aborted] {llm_ctx.abort_reason}"
    SESSION_MANAGER.add_message({"role": "assistant", "content": abort_text})
    render_background_notification(abort_text, title="⛔ Extension Abort")
    return

try:
    stream = await self.call_llm(messages=llm_ctx.messages, tools=llm_ctx.tools,
                                 max_tokens=llm_ctx.max_tokens, ctx=ctx)
```

`run()` 中 `state.has_escalated = False` 之后、`if not tool_calls:` 之前插入：

```python
llm_ctx = AfterLLMContext(content=accumulated_text, tool_calls=tool_calls or [],
                          finish_reason=finish_reason or "", usage=usage)
await dispatch("after_llm", llm_ctx)
assistant_message["content"] = llm_ctx.content
tool_calls = llm_ctx.tool_calls
injected = llm_ctx.inject_messages
```

两个分支（无工具 / 有工具）在 `SESSION_MANAGER.add_message(assistant_message)` 之后各加：

```python
for message in injected:
    SESSION_MANAGER.add_message(message)
```

`call_tools()` 顶部加 `injected_messages: list[dict] = []`；把现有 `render_tool_call(tool_name, tool_args)` 与 `trigger_hooks` 一段替换为：

```python
tool_ctx = BeforeToolContext(tool_name=tool_name, args=tool_args)
await dispatch("before_tool", tool_ctx)
tool_args = tool_ctx.args
tool_call.function.arguments = json.dumps(tool_args, ensure_ascii=False)
render_tool_call(tool_name, tool_args)
if tool_ctx.blocked:
    tool_call_results.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": f"[Extension blocked] {tool_ctx.block_reason}"
    })
    continue

blocked = trigger_hooks("pre_tool_call", tool_call)
```

执行完成、`render_tool_result(result)` 之后、`tool_call_results.append` 之前插入：

```python
result_ctx = AfterToolContext(tool_name=tool_name, args=tool_args,
                              result=str(result), is_error=tool_failed)
await dispatch("after_tool", result_ctx)
injected_messages.extend(result_ctx.inject_messages)
```

该 append 的 `"content"` 改用 `result_ctx.result`；`finally` 里工具结果写回循环之后追加：

```python
for message in injected_messages:
    SESSION_MANAGER.add_message(message)
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `uv run pytest tests/test_extension_loop_wiring.py tests/test_agent_error_rendered.py tests/test_agent_interrupt.py tests/test_agent_turn_wiring.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/loop_with_interrupt.py tests/test_extension_loop_wiring.py
git commit -m "主循环接入扩展四节点：LLM/工具前后事件"
```

---

### Task 5: 子代理四节点接线

**Files:**
- Modify: `core/sub_agent.py`
- Test: `tests/test_extension_subagent_wiring.py`

**Interfaces:**
- Consumes: Task 3 的 `core.extension` 四个上下文类与 `dispatch`。
- Produces: `spawn_subagent` 在四个节点触发事件；`abort` 返回 `"(subagent aborted by extension: reason)"`。

- [ ] **Step 1: 写失败测试** `tests/test_extension_subagent_wiring.py`

```python
"""子代理四节点接线：abort、改参送达执行器、拦截写回、结果改写。"""
import asyncio

import pytest

import core.sub_agent as sa
import core.tools
import core.tools.tool_loader
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook

TOOL_ROUND = ("", "", [{"id": "t1", "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}],
              "tool_calls", None)
DONE_ROUND = ("done", "", [], "stop", None)


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


class _FakeModelClient:
    current_provider = "test"

    def __init__(self, calls):
        self.calls = calls

    def get_model_client(self, **_):
        async def create(**kwargs):
            self.calls.setdefault("model_messages", []).append(kwargs["messages"])
            return object()
        return create

    def clamp_max_tokens(self, requested):
        return requested


def _patch(monkeypatch, responses, execute):
    calls = {"args": []}

    async def _streaming_message(stream, ctx=None):
        return responses.pop(0)

    async def _execute_tool(handler, args, name, ctx):
        calls["args"].append(args)
        return execute

    async def _prepare(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _FakeModelClient(calls))
    monkeypatch.setattr(sa, "with_retry_async", lambda fn, provider="": fn())
    monkeypatch.setattr(sa, "prepare_messages", _prepare)
    monkeypatch.setattr(sa, "streaming_message", _streaming_message)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "trigger_hooks", lambda *a: None)
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: ([], {"terminal": object()}))
    monkeypatch.setattr(core.tools.tool_loader, "execute_tool", _execute_tool)
    return calls


def test_before_llm_abort_returns_notice(monkeypatch):
    responses = [DONE_ROUND]
    _patch(monkeypatch, responses, "unused")
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "(subagent aborted by extension: policy)"
    assert responses == [DONE_ROUND]  # 模型未被调用


def test_before_llm_mutation_reaches_model(monkeypatch):
    responses = [DONE_ROUND]
    calls = _patch(monkeypatch, responses, "unused")
    add_hook("before_llm", lambda ctx: ctx.messages.append({"role": "user", "content": "injected"}), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert any(m.get("content") == "injected" for m in calls["model_messages"][0])


def test_before_tool_args_mutation_reaches_executor(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert calls["args"] == [{"command": "ls -la"}]


def test_before_tool_block_writes_result(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert calls["args"] == []
    second_round = calls["model_messages"][1]
    tool_message = next(m for m in second_round if m.get("role") == "tool")
    assert tool_message["content"] == "[Extension blocked] no shell"


def test_after_tool_rewrites_result(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("after_tool", lambda ctx: setattr(ctx, "result", "processed"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    second_round = calls["model_messages"][1]
    tool_message = next(m for m in second_round if m.get("role") == "tool")
    assert tool_message["content"] == "processed"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_subagent_wiring.py -v`
Expected: FAIL（事件未接线）

- [ ] **Step 3: 修改 `core/sub_agent.py`**

import 区加：

```python
from core.extension import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, dispatch,
)
```

模型调用前（`prepare_messages` 之后）：

```python
sub_max_tokens = sub_client.clamp_max_tokens(CONFIGMANAGER.config.default_max_tokens)
llm_ctx = BeforeLLMContext(messages=messages, tools=tools, max_tokens=sub_max_tokens)
await dispatch("before_llm", llm_ctx)
if llm_ctx.aborted:
    return f"(subagent aborted by extension: {llm_ctx.abort_reason})"
```

`with_retry_async` 内部的 lambda 改用 `llm_ctx.messages` / `llm_ctx.tools` / `llm_ctx.max_tokens`。

`streaming_message` 返回后（不再丢弃后两位）：

```python
content, reasoning_content, tool_calls, finish_reason, usage = await streaming_message(stream)

llm_result = AfterLLMContext(content=content, tool_calls=tool_calls or [],
                             finish_reason=finish_reason or "", usage=usage)
await dispatch("after_llm", llm_result)
assistant_message = {
    "role": "assistant",
    "content": llm_result.content,
    "reasoning_content": reasoning_content,
}
if llm_result.tool_calls:
    assistant_message["tool_calls"] = llm_result.tool_calls
messages.append(assistant_message)
for message in llm_result.inject_messages:
    messages.append(message)

if not llm_result.tool_calls:
    return llm_result.content or "(subagent finished without a text conclusion)"
```

工具循环：`tool_calls` 换成 `llm_result.tool_calls`；每个 tool call 先解析参数再派发（与主循环一致）：

```python
name = tool_call["function"]["name"]
injected = []
try:
    tool_args = json.loads(tool_call["function"]["arguments"] or "{}")
except json.JSONDecodeError as e:
    output = f"[Error] {type(e).__name__}: {e}"
else:
    tool_ctx = BeforeToolContext(tool_name=name, args=tool_args)
    await dispatch("before_tool", tool_ctx)
    if tool_ctx.blocked:
        output = f"[Extension blocked] {tool_ctx.block_reason}"
    else:
        permission_call = ChatCompletionMessageToolCall(**tool_call)
        permission_call.function.arguments = json.dumps(tool_ctx.args, ensure_ascii=False)
        blocked = trigger_hooks("pre_tool_call", permission_call)
        if blocked:
            output = str(blocked)
        else:
            output = await execute_tool(handlers.get(name), tool_ctx.args, name, ctx)
            result_ctx = AfterToolContext(
                tool_name=name, args=tool_ctx.args, result=str(output),
                is_error=str(output).startswith((TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX)))
            await dispatch("after_tool", result_ctx)
            output = result_ctx.result
            injected = result_ctx.inject_messages

messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": str(output)})
for message in injected:
    messages.append(message)
```

同时 import 区补 `from core.template import TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX`。

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `uv run pytest tests/test_extension_subagent_wiring.py tests/test_subagent_status.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/sub_agent.py tests/test_extension_subagent_wiring.py
git commit -m "子代理接入扩展四节点：LLM/工具前后事件"
```

---

### Task 6: 示例扩展与 README 文档

**Files:**
- Create: `examples/extension_example/extension.json`
- Create: `examples/extension_example/__init__.py`
- Modify: `README.md`
- Test: `tests/test_extension_loader.py`（追加示例可加载测试）

**Interfaces:**
- Consumes: Task 1–3 的 API 与加载器。
- Produces: 可直接复制到 `.harness/extensions/` 的最小示例；README 扩展章节。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_extension_loader.py`）

```python
def test_example_extension_loads(tmp_path):
    import shutil
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "examples" / "extension_example"
    shutil.copytree(source, tmp_path / "extension_example")
    load_extensions(tmp_path)
    assert _count("before_llm") >= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_loader.py::test_example_extension_loads -v`
Expected: FAIL（`FileNotFoundError: examples/extension_example`）

- [ ] **Step 3: 创建示例与 README 章节**

`examples/extension_example/extension.json`：

```json
{
    "enabled": true
}
```

`examples/extension_example/__init__.py`：最小可用示例，必须定义 `register(api)`；注册 `before_llm`（同步）与 `after_tool`（异步）各一个，行为为追加标记文本，便于冒烟肉眼观察，同时注释说明复制到 `.harness/extensions/` 后重启生效。

`README.md` 新增"扩展（extensions）"一节，必须覆盖：
- 目录结构：`.harness/extensions/<name>/{extension.json, __init__.py}`，`enabled` 严格布尔 `true` 才加载，修改后需重启。
- API：`register(api)`、`api.on(event, callback)`，sync/async 回调均可；四事件与可写字段摘要表（`before_llm`：messages/tools/max_tokens + abort；`after_llm`：content/tool_calls/inject_messages；`before_tool`：args + block；`after_tool`：result/inject_messages）。
- 失败语义 fail-open、信任模型（扩展以完整进程权限运行）、明确排除路径（后台任务执行结果、压缩/标题等辅助 LLM 调用、旧 `.hooks.json` 不参与）。
- 指向 `docs/superpowers/specs/2026-10-08-extension-hooks-design.md`。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `uv run pytest tests/ -q`
Expected: PASS（全量）

- [ ] **Step 5: 提交**

```bash
git add examples/extension_example README.md tests/test_extension_loader.py
git commit -m "新增扩展示例与 README 扩展文档"
```
