# 外部扩展 tool_execute 接管节点实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在扩展系统新增第五个介入节点 `tool_execute`，让扩展可接管内置工具的执行；无人接管或接管失败时，内置路径行为不变。

**Architecture:** 纯增量接线：`core/extension/` 加一个事件、一个上下文与派发器 `handled` 短路；`core/agent_step.py::_run_tools` 在权限门之后、后台路由之前派发 `tool_execute`，接管则跳过「后台路由 + diff 预览 + `pool.execute`」并汇入原有结果处理。

**Tech Stack:** Python 3.13、asyncio、dataclasses、pytest；命令 `uv run pytest tests/`。

**Spec:** `docs/superpowers/specs/2026-10-11-extension-tool-execute-design.md`

## Global Constraints

- 事件名固定 `tool_execute`；上下文固定 `ToolExecuteContext(tool_name, args, tctx=None)`；接管唯一入口 `ctx.handle(result, is_error=False)`，只写 `ctx.result` 不生效。
- 节点顺序固定：`before_tool` → `renderer.on_tool_call` → 权限门 `pre_tool_call` → `tool_execute` → 后台路由 / diff 预览 / `pool.execute` → `after_tool`。
- 接管时跳过：后台路由、diff 预览、`pool.execute`；`after_tool`、`on_tool_result` 照常作用于接管结果。
- fail-open：每个回调只 `except Exception`，不得捕获 `CancelledError`/`BaseException`；接管回调异常视为未接管、回退内置执行。
- 不改 `core/tools/`、`core/hook/hook.py`、`core/background_task.py`；不改现有四节点语义。
- 提交信息用中文；每个任务结束跑该任务测试与相关回归后提交。

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `core/extension/context.py` | 事件上下文 dataclass | 修改（+`ToolExecuteContext`） |
| `core/extension/api.py` | 事件名与注册缓冲 | 修改（`EVENTS` +1） |
| `core/extension/dispatcher.py` | 链式派发与短路 | 修改（短路条件 +`handled`） |
| `core/extension/__init__.py` | 包导出 | 修改（导出上下文） |
| `core/agent_step.py` | 共享 agent step 的工具派发 | 修改（`_run_tools` 接线） |
| `tests/test_extension_api.py` | 事件与上下文契约测试 | 修改 |
| `tests/test_extension_dispatcher.py` | 派发语义测试 | 修改 |
| `tests/test_extension_tool_execute_wiring.py` | 接线行为测试 | 新建 |
| `docs/extensions.md` 等 | 用户文档 | 修改 |

## Review Focus

1. 扩展只写 `ctx.result` 未调 `handle()`：内置必须照常执行（Task 1 `test_tool_execute_result_without_handle_is_not_takeover`；Task 3 `test_without_handle_builtin_still_runs`）。
2. 接管本会后台化的工具（慢 `terminal` / `spawn_subagent`）：不得进入后台路由（Task 3 `test_handled_tool_skips_background_routing`）。
3. `tool_execute` 回调抛异常：回退内置；`CancelledError` 不得被吞（Task 2 现有 `test_cancelled_error_propagates`；Task 3 `test_hook_exception_falls_back_to_builtin`）。
4. 接管 `write_file` / `edit_file`：不生成 diff 预览、消息无 `payload`（Task 3 `test_handled_write_file_skips_diff_preview`）。
5. `before_tool` block 与权限门：优先于 `tool_execute`，接管不能绕过（Task 3 `test_before_tool_block_precedes_tool_execute`、`test_permission_gate_runs_before_tool_execute`）。

---

### Task 1: 事件与接管上下文契约

**Files:**
- Modify: `core/extension/context.py`
- Modify: `core/extension/api.py:4`
- Modify: `core/extension/__init__.py`
- Test: `tests/test_extension_api.py`

**Interfaces:**
- Consumes: `core.runtime_context.ToolContext`（已有）。
- Produces（`core/extension/context.py`）：
  - `ToolExecuteContext(tool_name: str, args: dict, tctx: ToolContext | None = None)`，字段 `handled: bool = False`、`result: str = ""`、`is_error: bool = False`，方法 `handle(result, is_error: bool = False) -> None`（`handled=True`、`result=str(result)`、`is_error=bool(is_error)`）。
  - `core/extension/api.py` 的 `EVENTS` 五项：`("before_llm", "after_llm", "before_tool", "tool_execute", "after_tool")`。
  - `core/extension.ToolExecuteContext` 可导入。

- [ ] **Step 1: 写失败测试** `tests/test_extension_api.py`

在文件顶部导入中加 `ToolExecuteContext`（与其它上下文类同一条 import）；把 `test_four_events_are_declared` 改名并改断言；追加三个用例：

```python
def test_five_events_are_declared():
    assert EVENTS == ("before_llm", "after_llm", "before_tool", "tool_execute", "after_tool")


def test_tool_execute_handle_captures_state_and_normalizes():
    ctx = ToolExecuteContext(tool_name="web_search", args={"query": "x"})
    assert (ctx.handled, ctx.result, ctx.is_error) == (False, "", False)
    ctx.handle(42, is_error=1)
    assert (ctx.handled, ctx.result, ctx.is_error) == (True, "42", True)


def test_tool_execute_result_without_handle_is_not_takeover():
    ctx = ToolExecuteContext(tool_name="web_search", args={})
    ctx.result = "ignored"
    assert ctx.handled is False


def test_tool_execute_context_is_exported_from_package():
    from core.extension import ToolExecuteContext as Exported
    assert Exported is ToolExecuteContext
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_api.py -v`
Expected: FAIL（`ImportError: cannot import name 'ToolExecuteContext'` 或 EVENTS 断言不符）

- [ ] **Step 3: 实现**

`context.py`：新增 `ToolExecuteContext` dataclass（字段顺序按 Interfaces：无默认值在前），`handle` 内做归一。
`api.py`：`EVENTS` 按上面改为五项（其余不动）。
`__init__.py`：import 并在 `__all__` 加入 `ToolExecuteContext`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_api.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add core/extension/context.py core/extension/api.py core/extension/__init__.py tests/test_extension_api.py
git commit -m "扩展系统：新增 tool_execute 事件与接管上下文"
```

---

### Task 2: 派发器 handled 短路

**Files:**
- Modify: `core/extension/dispatcher.py:32-33`（`dispatch` 循环内的短路判断）
- Test: `tests/test_extension_dispatcher.py`

**Interfaces:**
- Consumes: Task 1 的 `ToolExecuteContext`。
- Produces: `dispatch` 在 `ctx.handled` 为真时停止该事件的剩余回调（与 `blocked` / `aborted` 同规则，用 `getattr` 判断以免影响其它上下文）。

- [ ] **Step 1: 写失败测试** `tests/test_extension_dispatcher.py`

导入 `ToolExecuteContext`，追加两个用例：

```python
def test_handled_short_circuits_remaining_hooks():
    calls = []

    def handles(ctx):
        ctx.handle("taken")

    dispatcher.add("tool_execute", handles, "ext_a")
    dispatcher.add("tool_execute", lambda ctx: calls.append("later"), "ext_a")
    ctx = ToolExecuteContext(tool_name="terminal", args={})
    _run("tool_execute", ctx)
    assert ctx.handled is True
    assert calls == []


def test_unhandled_runs_later_hooks():
    calls = []

    dispatcher.add("tool_execute", lambda ctx: None, "ext_a")
    dispatcher.add("tool_execute", lambda ctx: calls.append("later"), "ext_a")
    _run("tool_execute", ToolExecuteContext(tool_name="terminal", args={}))
    assert calls == ["later"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_dispatcher.py -v`
Expected: FAIL（`test_handled_short_circuits_remaining_hooks` 中 `calls == ["later"]`，短路未实现）

- [ ] **Step 3: 实现**

`dispatch` 内现有判断：

```python
        if getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False):
            break
```

改为：

```python
        if (getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False)
                or getattr(ctx, "handled", False)):
            break
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_dispatcher.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: 提交**

```bash
git add core/extension/dispatcher.py tests/test_extension_dispatcher.py
git commit -m "扩展派发器：handled 后短路剩余回调"
```

---

### Task 3: agent_step 接线与接管行为

**Files:**
- Modify: `core/agent_step.py`（import 行 + `_run_tools` 内执行段）
- Test: `tests/test_extension_tool_execute_wiring.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `ToolExecuteContext`；Task 2 的 `handled` 短路派发。
- Produces: `_run_tools` 行为——被接管的调用以 `ToolResult(exec_ctx.result, exec_ctx.is_error)` 作为执行结果，跳过后台路由 / diff 预览 / `pool.execute`；未接管与接管失败走原内置路径。无新增公开名字。

- [ ] **Step 1: 写失败测试** `tests/test_extension_tool_execute_wiring.py`

```python
"""tool_execute 接线：接管取代内置执行；未接管 / fail-open 回退内置。"""
import asyncio

import pytest

import core.agent_step as step_mod
from core.agent_step import StepPolicy, StepRenderer, run_agent_step
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook
from core.runtime_context import AgentRunContext, ToolContext
from core.tools import ToolPool
from core.tools.tool_base import BaseTool


class _Client:
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            return object()
        return create


def _pool(calls):
    class _Echo(BaseTool):
        value: str = ""

        def run(self, tctx=None):
            calls.append(self.value)
            return f"echo:{self.value}"

    class _WriteFile(BaseTool):
        path: str = ""
        content: str = ""

        def run(self, tctx=None):
            calls.append("write_file")
            return "written"

    return ToolPool({"echo": _Echo, "write_file": _WriteFile})


def _tool_call(name="echo", args='{"value": "x"}'):
    return {"id": "t1", "type": "function", "function": {"name": name, "arguments": args}}


def _run(monkeypatch, tool_call, calls, *, policy=None, renderer=None):
    async def fake_stream(stream, on_text=None):
        return ("", "", [tool_call], "tool_calls", None)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    return asyncio.run(run_agent_step(
        [],
        "sys",
        client=_Client(),
        pool=_pool(calls),
        tctx=ToolContext(agent_run=AgentRunContext()),
        max_tokens=100,
        policy=policy or StepPolicy(use_extensions=True, permission_hooks=False),
        renderer=renderer or StepRenderer(),
    ))


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


def test_handle_replaces_builtin_and_after_tool_sees_result(monkeypatch):
    seen = []

    def replace(ctx):
        if ctx.tool_name == "echo":
            ctx.handle("ext:x")

    add_hook("tool_execute", replace, "ext_a")
    add_hook("after_tool", lambda ctx: seen.append(ctx.result), "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == []                                     # 内置未执行
    assert outcome.followup_messages[0]["content"] == "ext:x"
    assert seen == ["ext:x"]                               # after_tool 可见


def test_handled_error_flag_reaches_renderer(monkeypatch):
    seen = []
    add_hook("tool_execute", lambda ctx: ctx.handle("[Error] remote down", is_error=True), "ext_a")
    renderer = StepRenderer(on_tool_result=lambda text, err: seen.append((text, err)))
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls, renderer=renderer)
    assert calls == []
    assert seen == [("[Error] remote down", True)]
    assert outcome.followup_messages[0]["content"] == "[Error] remote down"


def test_without_handle_builtin_still_runs(monkeypatch):
    def observe(ctx):
        assert ctx.tool_name == "echo"
        assert ctx.args == {"value": "x"}

    add_hook("tool_execute", observe, "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"


def test_hook_exception_falls_back_to_builtin(monkeypatch):
    def broken(ctx):
        raise RuntimeError("boom")

    add_hook("tool_execute", broken, "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"


def test_handled_tool_skips_background_routing(monkeypatch):
    started = []
    monkeypatch.setattr(step_mod, "should_run_background", lambda *a: True)
    monkeypatch.setattr(step_mod, "start_background_task",
                        lambda call, pool, tctx: started.append(call) or "bg-1")
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:x"), "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls,
                   policy=StepPolicy(use_extensions=True, permission_hooks=False, background=True))
    assert started == []
    assert outcome.followup_messages[0]["content"] == "ext:x"


def test_handled_write_file_skips_diff_preview(monkeypatch):
    previews = []
    monkeypatch.setattr(step_mod, "preview_write", lambda *a: previews.append(a) or "diff")
    diffs = []
    renderer = StepRenderer(on_diff=lambda d: diffs.append(d))
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:written"), "ext_a")
    calls = []
    outcome = _run(monkeypatch,
                   _tool_call("write_file", '{"path": "a.txt", "content": "hi"}'),
                   calls,
                   renderer=renderer,
                   policy=StepPolicy(use_extensions=True, permission_hooks=False, diff_preview=True))
    assert calls == []
    assert previews == [] and diffs == []
    assert outcome.followup_messages[0]["content"] == "ext:written"
    assert "payload" not in outcome.followup_messages[0]


def test_before_tool_block_precedes_tool_execute(monkeypatch):
    calls = []
    add_hook("before_tool", lambda ctx: ctx.block("no"), "ext_a")
    add_hook("tool_execute", lambda ctx: calls.append("execute"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), [])
    assert calls == []
    assert outcome.followup_messages[0]["content"] == "[Extension blocked] no"


def test_permission_gate_runs_before_tool_execute(monkeypatch):
    calls = []
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a, **k: "denied")
    add_hook("tool_execute", lambda ctx: calls.append("execute"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), [],
                   policy=StepPolicy(use_extensions=True, permission_hooks=True))
    assert calls == []
    assert outcome.followup_messages[0]["content"] == "[denied]"


def test_use_extensions_false_does_not_dispatch(monkeypatch):
    calls = []
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:x"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), calls,
                   policy=StepPolicy(use_extensions=False, permission_hooks=False))
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extension_tool_execute_wiring.py -v`
Expected: FAIL（`test_handle_replaces_builtin_and_after_tool_sees_result` 等内容为 `echo:x` 或派发器未知事件；接线尚不存在）

- [ ] **Step 3: 实现** `core/agent_step.py`

1. 在 `from core.extension import (...)` 中加入 `ToolExecuteContext`。
2. 在 `_run_tools` 中，把现有「后台路由 → `diff = ""` → `with renderer.working_status(): diff 预览 + pool.execute`」区段改为下面结构（`...` 处代码原样保留，仅移动缩进）：

```python
            diff = ""
            extension_result: ToolResult | None = None
            if policy.use_extensions:
                exec_ctx = ToolExecuteContext(tool_name=tool_name, args=tool_args, tctx=tctx)
                with renderer.working_status():
                    await dispatch("tool_execute", exec_ctx)
                if exec_ctx.handled:
                    extension_result = ToolResult(content=exec_ctx.result, is_error=exec_ctx.is_error)

            if extension_result is not None:
                result = extension_result
            elif policy.background and should_run_background(tool_name, tool_args):
                ...  # 原后台路由分支（含 continue）
            else:
                with renderer.working_status():
                    ...  # 原 diff 预览 + pool.execute + CancelledError 处理
```

3. 汇合段（`output = result.content` 起）与 `after_tool`、`renderer.on_tool_result`、消息写入全部不动；`message["payload"]` 的条件不变（接管路径 `diff == ""`，自然无 payload）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extension_tool_execute_wiring.py tests/test_agent_step.py tests/test_extension_loop_wiring.py tests/test_extension_subagent_wiring.py -v`
Expected: PASS（新文件 9 passed；相关回归全绿）

- [ ] **Step 5: 提交**

```bash
git add core/agent_step.py tests/test_extension_tool_execute_wiring.py
git commit -m "Agent step：tool_execute 节点接线，扩展可接管内置工具执行"
```

---

### Task 4: 文档同步

**Files:**
- Modify: `docs/extensions.md`
- Modify: `README.md:14`、`README.md:70-73`
- Modify: `docs/teams.md:7`

**Interfaces:**
- Consumes: Task 1-3 的最终事件名、上下文名与语义。
- Produces: 用户文档与实现一致，无「四个节点/四个事件」残留（历史 spec / plan 除外）。

- [ ] **Step 1: 更新 `docs/extensions.md`**

- 第 3 行：四个关键节点 → 五个，节点列表加「工具执行接管（`tool_execute`）」。
- §3.1 事件取值：加 `tool_execute`。
- §3.2 上下文表：加一行——事件 `tool_execute`；上下文 `ToolExecuteContext(tool_name, args, tctx=None)`；可写字段 **`result`**、**`is_error`**；控制方法 `ctx.handle(result, is_error=False)`；补充「只有 `handle()` 算接管」。
- §3.3 触发时机表：加一行——工具参数解析且权限门通过后、后台路由之前；接管则跳过内置执行。
- §4.2 短路控制：补 `handle()` 语义与「回调异常 fail-open → 视为未接管 → 回退内置执行」。
- §5 实战配方：新增「接管工具执行」示例（`api.on("tool_execute", ...)`，按 `ctx.tool_name` 判断后 `ctx.handle(...)`）。
- §6 排错：加「接管没生效」条目（只写 `ctx.result` 没调 `handle()`；回调异常被 fail-open 吞掉并回退内置）与副作用警示。
- §7 可测试性导出：四个上下文类 → 五个。
- §8 覆盖范围：四个事件 → 五个；补「接管时跳过后台路由与 diff 预览」「后台任务路径不二次派发」。
- §8 工具节点顺序：改为「外部 `before_tool` → 参数回写 → 旧权限门 → 外部 `tool_execute`（接管则跳过内置）→ 执行 → 外部 `after_tool`」。

- [ ] **Step 2: 更新 `README.md` 与 `docs/teams.md`**

- `README.md:14`：「外部扩展四个事件介入」→「五个事件介入」。
- `README.md:70-73`：扩展系统小节的节点描述补「工具执行接管（`tool_execute`）」。
- `docs/teams.md:7`：「扩展四节点开启」→「扩展五节点开启」。

- [ ] **Step 3: 验证无残留**

Run: `grep -rn "四个事件\|四个节点\|四个关键节点\|四节点\|四个上下文" README.md docs/extensions.md docs/teams.md GLOSSARY.md`
Expected: 无输出（历史 `docs/superpowers/specs/2026-10-08-*.md`、`docs/superpowers/plans/2026-10-08-*.md` 不在检查范围）

- [ ] **Step 4: 全量回归**

Run: `uv run pytest tests/`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add docs/extensions.md README.md docs/teams.md
git commit -m "文档：扩展系统同步 tool_execute 接管节点"
```

---

## 完成标准

- `uv run pytest tests/` 全绿。
- 五个事件契约与文档一致；接管时内置执行、后台路由、diff 预览均不发生；未接管与接管失败时行为与改动前一致。
