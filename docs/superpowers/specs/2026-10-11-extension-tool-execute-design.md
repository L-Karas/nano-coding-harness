# 外部扩展工具接管节点设计（nano-harness）

- 日期：2026-10-11
- 状态：待评审
- 关联代码：`core/extension/`、`core/agent_step.py`
- 前置设计：[`2026-10-08-extension-hooks-design.md`](2026-10-08-extension-hooks-design.md)

## 1. 背景与目标

现有扩展系统有四个介入节点（`before_llm` / `after_llm` / `before_tool` / `after_tool`），能力覆盖观察、改参、拦截与改写结果，但**不能替代工具的执行本身**：模型调用 `web_search` 时，真正执行的永远是 `ToolPool.execute` 里的内置实现，外部扩展最多在前后加工数据。

本设计新增第五个介入节点 `tool_execute`：模型照常调用内置工具（schema 不变），扩展可以**接管本次执行**，用自己的实现产生工具结果；无人接管时行为与现在完全一致。

### 已确认的设计决策

| 决策点 | 结论 |
|---|---|
| 注册形态 | 事件式：`api.on("tool_execute", callback)`，与现有四节点同构 |
| 接管方式 | `ctx.handle(result, is_error=False)` 显式接管；只写 `ctx.result` 不生效 |
| 失败语义 | fail-open：回调异常记日志、视为未接管；最终无人接管则内置执行（沿用现有信任模型） |
| 节点位置 | 权限门（`pre_tool_call`）之后、后台路由之前 |
| 接管时跳过 | 后台路由 + diff 预览 + `pool.execute` |
| 触发范围 | 参数解析成功的每个 tool call，含 MCP 工具与不在 pool 中的名字 |
| 生效范围 | main / sub-agent / teammate（共用 `run_agent_step`），受 `policy.use_extensions` 门控 |
| 权限门 | 照常先执行；扩展接管不能绕过旧权限检查 |
| `after_tool` | 两种来源（内置 / 接管）都触发，结果对其它扩展可见 |
| 后台路径 | 不二次派发；接管即不后台化 |

## 2. 非目标（本次不做）

- 扩展注册新工具 / 新工具 schema（只替换已存在工具名的**执行**，不改 `ToolPool` 装配）。
- 扩展自带 diff 预览 / payload（本版接管路径不生成预览、不写 `payload`）。
- 后台任务执行路径（`core/background_task.py`）二次派发 `tool_execute`。
- 运行时热切换 `enabled`（沿用现有「改扩展需重启」）。
- 现有四节点的语义调整。

## 3. API 契约

### 3.1 事件

```python
EVENTS: tuple[str, ...] = (
    "before_llm", "after_llm", "before_tool", "tool_execute", "after_tool",
)
```

`api.on("tool_execute", callback)` 走现有校验（未知事件名 `ValueError`、非 callable `TypeError`），无需新增注册通道；`ExtensionAPI` 与 loader 的原子提交逻辑不变。

### 3.2 上下文对象

新增 `core/extension/context.py::ToolExecuteContext`：

```python
@dataclass
class ToolExecuteContext:
    tool_name: str
    args: dict
    tctx: ToolContext | None = None      # 运行上下文：取消信号 / cwd / agent 身份
    handled: bool = False
    result: str = ""
    is_error: bool = False

    def handle(self, result, is_error: bool = False) -> None:
        """声明接管本次执行；结果取代内置执行。"""
        self.handled = True
        self.result = str(result)
        self.is_error = bool(is_error)
```

语义说明：

- `tool_name` / `args` 只读信息（`args` 是 `before_tool` 改写后的最终参数）；`tctx` 只读，替代实现可自查取消（`tctx.raise_if_cancelled()`）、取 `cwd` 与发起 agent 身份。
- **只有 `handle()` 才表示接管**；只写 `ctx.result` 不算接管，内置执行照常发生，避免「写了值但没生效」的歧义。
- 多个回调共享同一 context、按注册顺序链式执行；`handled` 后短路剩余回调（与 `blocked` / `aborted` 同规则）。
- `handle()` 内做 `str()` / `bool()` 归一；派发器现有 `result` 归一作为兜底。
- 回调返回值仍被忽略，只通过修改 `ctx` 生效。

### 3.3 派发语义

- 顺序：扩展文件夹名字典序 → 同一扩展内 `api.on()` 调用顺序。
- 短路：`dispatcher.dispatch` 的循环结束条件扩为 `ctx.blocked or ctx.aborted or ctx.handled`（用 `getattr(ctx, "handled", False)`，不影响其它上下文）。
- fail-open：每个回调独立 `try/except Exception`，异常记 `dispatcher.log` 后视为未接管、继续后续回调；`asyncio.CancelledError` 不捕获，Esc 中断正常传播。
- 无超时机制：接管回调即真实执行体，长耗时请自行异步化（沿用现有边界）。

## 4. 核心接线

### 4.1 `core/extension/api.py`（1 行）

`EVENTS` 加 `"tool_execute"`。

### 4.2 `core/extension/dispatcher.py`（1 行）

```python
if getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False) or getattr(ctx, "handled", False):
    break
```

### 4.3 `core/extension/context.py` / `__init__.py`

新增并导出 `ToolExecuteContext`（加入 `__all__`）。

### 4.4 `core/agent_step.py::_run_tools`

节点插在权限门之后、后台路由之前；接管时跳过「后台路由 + diff 预览 + `pool.execute`」，其余环节不变：

```
before_tool（扩展，可改参 / block）
→ renderer.on_tool_call
→ 权限门 pre_tool_call（不动）
→ tool_execute（扩展）：
     handle() → result = ToolResult(ctx.result, ctx.is_error)
              → 跳过【后台路由 + diff 预览 + pool.execute】
     无人 handle → 以下全部照旧
→ 后台路由 / diff 预览 / pool.execute
→ after_tool（不动，两种来源都触发）
→ renderer.on_tool_result → 写消息（接管路径无 diff payload）
```

结构示意（相对现有代码的增量）：

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
                ...  # 后台路由，原样
                continue
            else:
                with renderer.working_status():
                    ...  # diff 预览 + pool.execute + 中断处理，原样

            output = result.content
            ...  # after_tool / 渲染 / 写消息，原样
```

### 4.5 边界规则

| 场景 | 行为 |
|---|---|
| 接管成功（`is_error=False`） | 结果写回 tool 消息；触发 `after_tool`、`on_tool_result`；无 `payload` |
| 接管且 `is_error=True` | 同上，结构标志为失败；触发 `should_halt` 判定 |
| 无人接管 | 后台路由 / diff 预览 / 内置执行全部照旧 |
| 接管了 `write_file` / `edit_file` | 不生成 diff 预览、不展示 diff（本版决策） |
| 接管了本会后台化的工具（如 `spawn_subagent`、慢 `terminal`） | 不后台化，接管结果同步返回 |
| `before_tool` 已 `block` | 不触发 `tool_execute`，结果仍为 `[Extension blocked] ...` |
| 参数 JSON 解析失败 / 中断占位 | 不触发 `tool_execute`（现有占位路径不变） |
| 未知工具名 | `tool_execute` 照常触发；无人接管时仍是 `ToolResult.unknown` |
| `policy.use_extensions=False` | 不派发，行为与现在完全一致 |
| 回调抛异常 | fail-open：视为未接管，最终可能回退执行内置实现；文档警示副作用风险 |

## 5. 错误处理与信任模型

- 与现有四节点一致：扩展在 agent 进程内以完整权限运行，只放可信代码；回调异常 fail-open、不阻断 agent。
- 「接管失败回退内置」是刻意选择：扩展若想失败闭合，自行 `try/except` 后 `ctx.handle(f"[my_ext error] {e}", is_error=True)`。
- 权限门仍在节点之前：扩展接管不能绕过旧权限检查（安全不变量不变）。

## 6. 测试策略

命令：`uv run pytest tests/`。

- `tests/test_extension_api.py`：`EVENTS` 断言更新为五元组；`handle()` 设置 `handled/result/is_error` 与类型归一。
- `tests/test_extension_dispatcher.py`：`handled` 短路剩余回调；未 `handle` 不短路；`result` 归一兜底。
- 新增 `tests/test_extension_tool_execute_wiring.py`（复用 `tests/test_agent_step.py` 的 stub 模型 / pool 模式）：
  - 接管 → `pool.execute` 未被调用、结果与 `is_error` 写入 tool 消息、`after_tool` 看到接管结果；
  - 无人接管 → 内置路径不变；
  - 回调抛异常 → 回退内置执行；
  - 接管跳过后台路由与 diff 预览（接管 `spawn_subagent` / `write_file` 断言）；
  - `before_tool` block 优先级高于 `tool_execute`；
  - `use_extensions=False` 不派发。
- 回归：现有扩展相关测试全绿；`test_four_events_are_declared` 随契约更新。

## 7. 文档更新

- `docs/extensions.md`：简介与节点表加第五节点；`EVENTS` / 上下文表 / 时机表 / 执行语义（`handle` 短路、fail-open 回退）同步；新增「接管工具执行」配方与排错项（接管没生效 → 检查是否调用了 `handle()`）。
- `README.md`：第 14 行「四个事件」→「五个事件」；扩展系统小节的节点描述补充「工具执行接管」。
- `docs/teams.md`：第 7 行「扩展四节点开启」→「扩展五节点开启」。
- 本 spec；`GLOSSARY.md` 无需新增术语（`ToolExecuteContext` 是机制对象的命名，非领域词汇）。

## 8. 变更文件清单

新增：

- `docs/superpowers/specs/2026-10-11-extension-tool-execute-design.md`
- `tests/test_extension_tool_execute_wiring.py`

修改：

- `core/extension/api.py`：`EVENTS` +1
- `core/extension/context.py`：+`ToolExecuteContext`
- `core/extension/dispatcher.py`：短路条件 +`handled`
- `core/extension/__init__.py`：导出 `ToolExecuteContext`
- `core/agent_step.py`：`_run_tools` 节点接线（约 15 行增量）
- `tests/test_extension_api.py`、`tests/test_extension_dispatcher.py`：契约更新 + 新用例
- `docs/extensions.md`、`README.md`、`docs/teams.md`：文档同步

不改动：

- `core/tools/`（`ToolPool`、工具类、schema 装配）
- `core/hook/hook.py`（权限门与旧 hook 机制）
- `core/background_task.py`
