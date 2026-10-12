# 同轮工具派发：并发白名单分组 + 屏障

## Status

accepted

同一轮内的多个工具调用不再逐个串行：`core/agent_step.py` 的 `group_parallel_tool_calls()` 把连续出现的 `parallel_safe` 工具收成一组，组内用 `asyncio.gather` 并发执行；未声明的工具（写文件、终端等）作为屏障自成一组，组间保持串行。每组固定三段：准备（串行：中断检查 → 解析 → `before_tool` → 渲染调用 → permission `pre_tool_call` → `tool_execute` → 后台路由）→ 执行（组内并发，每个 task 经 `AgentRunContext.track` 登记）→ 收尾（串行：按原调用顺序触发 `after_tool`、渲染结果、回填 tool 消息）。因此扩展回调与渲染器始终运行在串行的准备 / 收尾段，不要求并发安全；权限确认不会与 Working 状态行同屏；tool 消息严格保持原 tool_calls 顺序；Esc 同时取消 turn task 与组内 task，`_run_tools` 在 gather 处捕获 `CancelledError` 后按中断占位收尾（组内未收尾的调用补 `[User interrupted]`，不丢弃整轮消息）；halt 由策略回调判定：命中后本组已并发的调用照常收尾（`after_tool` / 渲染 / 回填结果），后续组不再执行，当前唯一使用方（teammate 的 `submit_plan`）落在单元素屏障组。并发安全性由各工具类上的 `BaseTool.parallel_safe` 字段声明（默认 False，`ToolPool.is_parallel_safe()` 查询；`_McpEntry` 默认 False），`group_parallel_tool_calls()` 按声明分组，不再依赖硬编码名字；当前声明为 True 的是 `read_file` / `glob` / `grep` / `list_crons` / `load_skill` / `save_memory` / `spawn_subagent` / `web_search` / `web_extract` 9 个（名单记录现状，新增工具自行声明）。其中原生异步的只有 `spawn_subagent` / `web_search` / `web_extract`；其余同步工具仍在事件循环上逐个执行，v1 不做 `to_thread` 包装。

> 注：`halt`（由 `StepPolicy.should_halt` 判定）与 teammate 的 `submit_plan` 后续已移除；并发分组与屏障语义不变。

## Considered Options

- **白名单分组 + 三段式串行边界**（采纳）：真正能并行等待的是原生异步的 I/O 工具（`web_search` / `web_extract` / `spawn_subagent`）；`read` / `grep` / `glob` 等只读同步工具声明后可与它们同组，v1 仍逐个执行；扩展与 TUI 没有并发契约，所以把并发严格限制在执行段。
- **整条 per-call 管线并发（含扩展钩子与渲染）**（拒绝）：要求扩展作者与渲染回调并发安全，`inject_messages` 顺序不确定，为几个只读工具付这个复杂度不划算。
- **全工具 `gather`**（拒绝）：写文件 / 终端共享工作目录与审批语义，必须作屏障；全并发还会让 halt 的生效边界不可判定。
- **把名单收进 `BaseTool.parallel_safe` 字段**（已采纳，后续实现）：方向符合 ADR-0002「Tool 即它的类」；工具类自己声明并发安全性，`ToolPool` 只负责查询，分组函数不再依赖硬编码名字。
