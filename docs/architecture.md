# 架构与主循环

## core/ 模块地图

| 路径 | 职责 |
| --- | --- |
| `main.py` | 入口：`start_agent_runtime()` 接真实 agent 回合，交给 Textual UI |
| `core/loop_with_interrupt.py` | Agent 主循环 `AgentRuntime`：LLM 流式调用、工具分发、中断、`/compact` |
| `core/config.py` | 路径常量与 `AgentConfig` 运行参数（`/settings` 编辑） |
| `core/bootstrap.py` | 启动初始化（composition root）：`.harness` 目录树/文件、hook、技能、模型注册表、cron 线程；`bootstrap()` 幂等且线程安全 |
| `core/interaction.py` | 域层 → UI 交互端口（permission / clarify）：TUI 导入渲染桥时注册，headless 默认拒绝/取消 |
| `core/runtime_context.py` | `AgentRunContext`（取消事件 + 任务登记）与 `AgentInterrupted` |
| `core/context/prompt.py` + `core/template/` | agent 级提示词（`prompt_template.py`：系统 / 子代理 / 摘要）与消息级模板（`message_template.py`：注入消息包装、续跑/中断提示、工具错误前缀、工具结果落盘/截断包裹） |
| `core/client/model.py` | `ModelClient` 与进程内单例 `shared_model_client()`；provider/模型/思考档位 |
| `core/context/session/session.py` | `SessionManager`：会话索引、消息落盘、payload 回写 |
| `core/tools/` | 工具基类、注册表与执行器；`base_tools/`（基础工具）、`extra_tools/`（扩展工具） |
| `core/context/compact/context_compact.py` | 两层上下文压缩（工具结果截断 / 历史摘要）与 token 估算 |
| `core/context/truncate.py` | 工具输出截断（2000 行 / 50KB 双上限），read 工具与上下文压缩共用 |
| `core/background_task.py` | 慢工具转后台执行，结果回流注入 |
| `core/cron_scheduler.py` | 5 段式 cron 解析、队列、持久化 |
| `core/hook/hook.py` | `pre_tool_call` / `post_tool_call` hook 与权限策略 |
| `core/recovery/error_recovery.py` | 按 provider 分类错误、指数退避重试、恢复状态 |
| `core/mcp/mcp_client.py` | MCP Server 连接与工具合并（独立后台事件循环） |
| `core/skill/skills.py` | 扫描 global（`~/.agents/skills`）/ user（`.harness/skills`）/ project（`.agents/skills`）三来源的 `SKILL.md`，同名 project > user > global |
| `core/context/memory/memory.py` | 长期记忆的增删查，注入系统提示词 |
| `core/sub_agent.py` | 子代理：独立提示词与工具池，最多 30 轮，只回传最终文本 |
| `core/experimental/` | `teammates.py`（自治队友线程）、`message_bus.py`（JSONL 邮箱）、`protocol_state.py`（请求状态）、`task.py`（任务板：任务 JSON、`blockedBy` 依赖、认领/完成）、`worktree/worktree.py`（git worktree 创建/移除/保留） |
| `core/tui/` | Textual 界面：App 装配、卡片/流式渲染、指令与弹窗、左栏分区与右栏面板（详见 [`tui.md`](tui.md)） |
| `smoke/` | TUI 无头冒烟自检（`python -m core.tui.ui_textual --smoke`）与离线演示 agent；不随 `core/` 发布 |
| `docs/` | 项目文档（见 [README 文档索引](../README.md#文档索引)） |
| `examples/` | s01–s18 教学脚本（从 agent loop 到 worktree 隔离的演进示例） |

## Agent 主循环

`AgentRuntime.run()` 每轮：

1. 取出 cron 队列并作为注入消息入会话，收集后台任务结果注入；
2. `prepare_messages()` 走上下文预算流水线（先截断工具结果，再按需摘要历史）；
3. 重建工具池（内置 + MCP）并流式请求模型，实时渲染思考/正文/工具调用增量和 usage；
4. 逐个执行工具调用（`PreToolUse` hook → 慢工具转后台 → 异步执行 → 渲染 diff/结果），
   结果写回会话后进入下一轮；
5. 模型不再请求工具即结束回合。

健壮性设计：

- **中断**：`Esc → AgentRuntime.interrupt()` 同时置位 `AgentRunContext.cancelled` 并取消任务，
  取消统一收敛为 `AgentInterrupted`；回合收尾会给未回答的 tool call 补一条中断占位结果，保证消息历史合法。
- **串行**：`AGENT_LOCK` 保证用户回合、cron 自动回合、`/compact` 三者互斥。
- **截断恢复**：`finish_reason == "length"` 先把 `max_tokens` 升到 `escalated_max_tokens`
  重试一次，仍截断则追加续写提示（最多 `max_recovery_retries` 次）。
- **错误重试**：`core/recovery` 按 provider 把异常分类（限流 / 模型过载 / 上下文过长 / 不可恢复），
  限流类指数退避重试（最多 `max_retries` 次）。
- **会话持久化**：每条消息即时落盘 `.harness/.session/`，工具结果携带的 diff 以 `payload` 保存在会话里，
  回放历史与 `/compact` 回写时都能还原。

## 上下文压缩（两层）

`prepare_messages()` 每轮按顺序执行，只有最后一层会调用模型：

1. `truncate_large_tool_outputs`：所有工具消息统一按 read 的行数 / 字节上限
   （2000 行 / 50KB，`core/context/truncate.py`）截断。`read_file` 输出已在工具侧截断并自带
   offset 续读提示，不落盘也不二次截断；其余工具超限时全文落盘到
   `.harness/.task_outputs/tool_results/`，正文替换为「截断内容 + `<saved-path>`」的
   `<persisted-output>` 包裹，提示中的 `offset=N` 与落盘全文行号对齐，可用
   `read_file <saved-path> offset=N` 续读，避免重新调用工具重新获取结果；
2. `compact_history`：估算 token > `compact_threshold`（默认 0.5 × 当前模型上下文长度，且不超过
   `上下文 − 最大输出`）时，按 user 消息切分轮次，从后往前保留 ≤ `reserve_threshold`
   （≤1 时为当前模型上下文长度的比例）的历史，其余交给模型总结成 `<compacted_messages>` 摘要。

阈值来自 `AgentConfig`（默认值见 `core/config.py`），并在运行中按当前模型动态解析，
切换模型（`/model`）后立即生效；`compact_threshold` / `reserve_threshold` / `summary_max_tokens`
等参数说明见 [`configuration.md`](configuration.md#上下文压缩参数)。

`/compact` 走 `AgentRuntime.compact()`：对整个会话做一次全量摘要后替换（中断则不落盘；
低于压缩阈值 / 未选择模型时不压缩，UI 提示 Nothing to compact）。

## 已知边界

- 同一轮内的多个工具调用目前串行执行（代码中留有 TaskGroup 并发化的 TODO）；
- 工具结果仅取文本内容，图片/多模态读取未实现；
- 权限策略为进程内 hook，`.hooks.json` 尚未被代码读取；
- `request_shutdown` / `request_plan` / `review_plan` 的审批闭环可跑通，但异常路径仍在打磨。
