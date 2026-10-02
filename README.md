# nano-harness

一个用 Python 3.13 + Textual 写的 coding agent harness：全屏 TUI 对话、流式工具调用、
分层上下文压缩、后台任务、cron 调度，以及子代理 / 自治队友 / 任务板 / git worktree 隔离。
源码即文档，全部实现位于 `core/`。

## 快速开始

```bash
uv sync          # 安装依赖（requires-python >= 3.13）
uv run main.py   # 启动 Textual TUI
```

首次启动会在项目根自动创建 `.harness/` 配置与数据目录（见下文）。
模型未配置时界面可正常打开，先用 `/provider` 填 API Key，再 `/model` 选模型即可对话。

```bash
uv run pytest tests/                          # 单元测试
uv run python -m core.tui.ui_textual --smoke  # TUI 无头冒烟自检（渲染/流式/补全/弹窗/权限）
```

## core/ 模块地图

| 路径 | 职责 |
| --- | --- |
| `main.py` | 入口：`start_agent_runtime()` 接真实 agent 回合，交给 Textual UI |
| `core/loop_with_interrupt.py` | Agent 主循环 `AgentRuntime`：LLM 流式调用、工具分发、中断、`/compact` |
| `core/config.py` | 路径常量与上下文预算参数；导入时创建 `.harness/` 目录树 |
| `core/runtime_context.py` | `AgentRunContext`（取消事件 + 任务登记）与 `AgentInterrupted` |
| `core/prompt.py` + `core/template/` | 系统提示词模板（主代理 / 子代理）、压缩摘要模板、注入消息包装 |
| `core/client/model.py` | `ModelClient` 与进程内单例 `shared_model_client()`；provider/模型/思考档位 |
| `core/session/session.py` | `SessionManager`：会话索引、消息落盘、payload 回写 |
| `core/tools/` | 工具基类、注册表与执行器；`base_tools/`（基础工具）、`extra_tools/`（扩展工具） |
| `core/compact/context_compact.py` | 三层上下文压缩与 token 估算 |
| `core/background_task.py` | 慢工具转后台执行，结果回流注入 |
| `core/cron_scheduler.py` | 5 段式 cron 解析、队列、持久化 |
| `core/hook/hook.py` | `pre_tool_call` / `post_tool_call` hook 与权限策略 |
| `core/recovery/error_recovery.py` | 按 provider 分类错误、指数退避重试、恢复状态 |
| `core/mcp/mcp_client.py` | MCP Server 连接与工具合并（独立后台事件循环） |
| `core/skill/skills.py` | 扫描 `.harness/skills/` 下的 `SKILL.md` |
| `core/memory/memory.py` | 长期记忆的增删查，注入系统提示词 |
| `core/task.py` | 任务板：任务 JSON、`blockedBy` 依赖、认领/完成 |
| `core/sub_agent.py` | 子代理：独立提示词与工具池，最多 30 轮，只回传最终文本 |
| `core/experimental/` | `teammates.py`（自治队友线程）、`message_bus.py`（JSONL 邮箱）、`protocol_state.py`（请求状态） |
| `core/worktree/worktree.py` | git worktree 创建/移除/保留 |
| `core/tui/` | Textual 界面：`ui_textual.py`（App）、`render.py`（线程安全渲染）、`widgets.py`（输入框与补全）、`screens/`（会话 / 模型 / MCP / 登录等弹窗）、`panels.py`（左栏分区）、`cards.py`（折叠卡片）、`info_panel.py`（右栏信息面板）、`smoke/`（冒烟自检）、`theme.py`/`app.css`、`demo.py` |
| `docs/` | 补充笔记：cron 表达式、harness 配置文件、asyncio |
| `examples/` | s01–s18 教学脚本（从 agent loop 到 worktree 隔离的演进示例） |

## 配置与数据目录 `.harness/`

首次导入 `core.config` 时自动创建：

| 路径 | 内容 |
| --- | --- |
| `.harness/.setting.json` | 默认 `provider` / `model` / `thinking_level`（`/provider`、`/model`、`/effort` 写入） |
| `.harness/.auth.json` | 各 provider 的 `api_key` |
| `.harness/.mcp/.mcp.json` | MCP Server 配置（`mcpServers`） |
| `.harness/skills/<name>/SKILL.md` | 技能清单（YAML frontmatter + 正文） |
| `.harness/.memory/*.json` | 长期记忆条目 |
| `.harness/.session/` | `session-<ts>.jsonl` 会话文件 + `session_index.jsonl` 索引 |
| `.harness/.tasks/task_*.json` | 任务板数据 |
| `.harness/.scheduled_tasks.json` | 持久化 cron 任务 |
| `.harness/.worktrees/` | worktree 目录与 `events.jsonl` 事件日志 |
| `.harness/.mailboxes/*.jsonl` | 队友消息邮箱（追加写，读取即清空） |
| `.harness/.task_outputs/tool_results/` | 超长工具输出落盘文件 |
| `.harness/log/<module>.log` | 各模块日志 |

## 模型配置

`core/client/models.json` 内置 provider 清单（Deepseek、Qwen、Kimi、Z.AI），
每个模型声明 `vision` / `thinking` / `thinking_level_map`（档位 → `reasoning_effort`），
`thinking_level_map` 中值为 `false` 表示该模型不支持此档位（此时不发送 `reasoning_effort`）。

- `/provider`：填/删 API Key（写入 `.auth.json`）
- `/model`：从已配置 provider 的模型列表中切换（写入 `.setting.json`）
- `/effort`：切换思考档位（`minimal` / `low` / `medium` / `high` / `max`）
- `/login`（别名 `/logout`）：注册 / 注销自定义提供方与模型（写入 `.harness/.custom_providers.json` /
  `.harness/.custom_models.json`）

所有 LLM 调用共用单例 `core.client.shared_model_client()`：主循环、子代理、上下文压缩、
teammate 走同一份配置，不存在第二处模型来源。

## TUI 交互

左右分栏（约 4:1）：左侧标题栏 + 卡片式聊天记录 + 状态行 + 输入条 + 页脚（cwd / git 分支 / 当前模型）；
右侧 Information 面板实时展示 Todos（`in_progress` 转圈）与后台任务（`running` 转圈），分区与条目均可点击折叠/展开。

| 操作 | 说明 |
| --- | --- |
| `Enter` | 无候选时发送消息；有 `/` 指令候选时先补全再发送 |
| `Shift+Enter` / `Ctrl+J` / `Ctrl+Enter` | 输入换行（`Tab` 不缩进，直接被吞掉） |
| `/` | 弹出指令候选，`Tab`/`Enter` 补全，`↑`/`↓` 选择 |
| `@` | 弹出项目文件候选（相对路径，补进输入，不发送） |
| `Esc` | 收起候选；权限确认挂起时「拒绝」；否则中断当前回合或压缩 |
| `Ctrl+C` | 不退出（有选中文本时复制）；退出用 `/exit` 或 `Ctrl+Q` |

内置指令：`/new`（新会话）、`/sessions`（会话选择）、`/compact`（压缩当前上下文，可被 Esc 中断）、
`/fork`（列出当前会话的用户消息，选中后从该消息前分叉出新会话并重放历史，消息原文填回输入栏供修改）、
`/skills`（技能列表，选中即作为一轮对话发出）、`/mcp`（MCP server 名列表，Enter 查看该 server
的工具列表，`Insert` 弹出 JSON 配置窗、`Ctrl+S` 落盘，`Delete` 删除 server）、
`/provider`、`/model`、`/effort`、
`/login`（别名 `/logout`，注册/注销自定义提供方与模型）、`/exit`（别名 `/quit`）。

## Agent 主循环

`AgentRuntime.run()` 每轮：

1. 取出 cron 队列并作为注入消息入会话，收集后台任务结果注入；
2. `prepare_messages()` 走上下文预算流水线（三层压缩）；
3. 重建工具池（内置 + MCP）并流式请求模型，实时渲染思考/正文/工具调用增量和 usage；
4. 逐个执行工具调用（`PreToolUse` hook → 慢工具转后台 → 异步执行 → 渲染 diff/结果），
   结果写回会话后进入下一轮；
5. 模型不再请求工具即结束回合。

健壮性设计：

- **中断**：`Esc → AgentRuntime.interrupt()` 同时置位 `AgentRunContext.cancelled` 并取消任务，
  取消统一收敛为 `AgentInterrupted`；回合收尾会给未回答的 tool call 补一条中断占位结果，保证消息历史合法。
- **串行**：`AGENT_LOCK` 保证用户回合、cron 自动回合、`/compact` 三者互斥。
- **截断恢复**：`finish_reason == "length"` 先把 `max_tokens` 从 8k 升到 16k 重试一次，
  仍截断则追加续写提示（最多 2 次）。
- **错误重试**：`core/recovery` 按 provider 把异常分类（限流 / 模型过载 / 上下文过长 / 不可恢复），
  限流类指数退避重试（最多 3 次）。
- **会话持久化**：每条消息即时落盘 `.harness/.session/`，工具结果携带的 diff 以 `payload` 保存在会话里，
  回放历史与 `/compact` 回写时都能还原。

## 上下文压缩（三层）

`prepare_messages()` 每轮按顺序执行，只有最后一层会调用模型：

1. `tool_result_budget`：单轮工具结果总量超 2MB 时，从最大的开始把全文落盘到
   `.harness/.task_outputs/tool_results/`，正文替换为前 3000 字符预览；
2. `micro_compact`：工具结果超过最近 30 条时，把更早且估算超过 2000 token 的结果清为占位文本；
3. `compact_history`：估算 token > 20 万时，按 user 消息切分轮次，从后往前保留 ≤ 2 万 token 的历史，
   其余交给模型总结成 `<compacted_messages>` 摘要。

`/compact` 走 `AgentRuntime.compact()`：对整个会话做一次全量摘要后替换（中断则不落盘）。

## 工具系统

工具即 `BaseTool` 子类（pydantic 模型）：类名 snake_case 即工具名，docstring 即工具描述，
字段即 JSON Schema；`agent_type` 集合决定该工具对哪些代理可见，
`run_<name>` / `run_<name>_async` 是同模块内的执行函数。新增工具只需在对应目录定义类与函数，
并在 `base_tools/__init__.py` 或 `extra_tools/__init__.py` 导出。

### 基础工具（main / sub-agent / teammate）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `bash` | `command`, `should_run_in_background` | shell 执行（同步路径 120s 超时），输出截断 5 万字符；`should_run_in_background=true` 或命中慢命令启发式时自动转后台 |
| `read_file` | `path`, `limit=2000`, `offset=1` | 分页读取文本，超限时返回续读 offset 提示 |
| `write_file` | `path`, `content` | 写文件，执行前渲染 diff 预览 |
| `edit_file` | `path`, `old_text`, `new_text` | 单次精确替换，执行前渲染 diff 预览 |
| `glob` | `pattern` | 按 glob 模式查找文件（异步路径优先 ripgrep 并尊重 .gitignore，缺失时回退 Python glob） |
| `grep` | `pattern`, `path`, `file_pattern` | 优先 ripgrep / grep，缺失时回退纯 Python 实现 |
| `clarify` | `options`, `multi_select=false` | 停靠区选项列表询问用户（main / sub-agent）：单选或 Space 多选 + Enter 确认，末尾 Other 可键入自定义回答，Esc 取消 |

文件类工具支持 `cwd` 注入，teammate 认领带 worktree 的任务后会切到对应目录执行。

### 扩展工具

| 工具 | 参数 | 可见 | 说明 |
| --- | --- | --- | --- |
| `todo_write` | `todos[{content,status}]` | main | 整体替换任务清单，右栏实时展示 |
| `spawn_subagent` | `description` | main | 独立上下文子代理，后台运行，最多 30 轮，结论经后台通知回传 |
| `load_skill` | `name` | main | 返回技能全文 |
| `save_memory` | `title`, `content`, `mem_type` | main | `mem_type`: user / feedback / project / reference |
| `create_task` | `subject`, `description`, `blockedBy` | main | 创建任务（可声明依赖） |
| `list_tasks` / `get_task` / `claim_task` / `complete_task` | `task_id` | main（后三者 teammate 亦可用） | 任务板：依赖未完成的任务不可认领，完成后回报解锁的任务 |
| `schedule_cron` | `cron_expression`, `prompt`, `recurring`, `durable` | main | 注册 5 段式 cron，触发时把 prompt 作为用户消息注入会话 |
| `list_crons` / `cancel_cron` | `job_id` | main | 查看 / 取消任务 |
| `spawn_teammate` | `name`, `role`, `prompt` | main | 启动自治队友线程（唯一名） |
| `send_message` | `to_agent`, `content` | main / teammate | 写入对方邮箱 |
| `check_inbox` | — | main / teammate | 读取并清空收件箱 |
| `request_shutdown` | `teammate` | main | 请求队友关闭（协议请求） |
| `request_plan` / `review_plan` | `teammate`,`task` / `request_id`,`approve`,`feedback` | main | 计划审批协议 |
| `submit_plan` | `plan` | teammate | 提交计划并挂起，等待 lead 审批后才继续 |
| `create_worktree` | `name`, `task_id` | main | `git worktree add .harness/.worktrees/<name> -b wt/<name>` |
| `remove_worktree` | `name`, `discard_changes` | main | 有未提交/未推送改动时默认拒删，需显式丢弃 |
| `keep_worktree` | `name` | main | 保留 worktree 供人工审查 |

MCP 工具以 `mcp__<server>__<tool>` 命名合并进同一工具池（仅 main / sub-agent）。

## 后台任务

`should_run_background()` 据此判定：`bash` 显式传 `should_run_in_background=true`，
或命令命中慢命令启发式（`make` / `pytest` / `sleep` / `pip install` / `npm test` / `cargo build` 等，
只看每条子命令的命令词，宁可漏判不误判）。

命中后立即返回 `[Background task <bg-id> started]` 占位结果，工具在守护线程中继续执行；
下一轮开始时结果以 `<background-task-notification>` 注入会话，右栏 Background Tasks 同步显示状态。

## Cron 调度

支持标准 5 段表达式：`*`、`*/N`、`a,b`、`a-b`，日期与星期同时限定时按 OR 语义匹配（详见 `docs/cron.md`）。
调度线程每秒匹配一次，命中的任务进入队列并记忆「分钟级」触发标记避免重复触发。
`durable=true` 的任务写入 `.harness/.scheduled_tasks.json` 并在进程启动时加载。
`cron_auto_loop` 后台线程取出队列任务后，在 `AGENT_LOCK` 内直接跑一整轮 agent 回合。

## 权限与 Hook

`core/hook/hook.py` 注册了三个 hook，`pre_tool_call` 在工具分发前运行：

- 硬拒绝（`DENY_LIST`）：清根删除、提权（`sudo`/`doas`）、关机重启、`mkfs`/`wipefs`、`dd` 与裸设备覆写、fork bomb、Windows `diskpart`/`format c:`，命中即拒；
- 危险命令（`DESTRUCTIVE`）：删除类（`rm`/`del`/`shred`…）、系统目录重定向、递归 chmod/chown、强杀进程、`git reset --hard`/强推、`crontab -r`、`curl | sh` 等管道执行，弹出确认卡，用户从停靠区 yes/no 列表作答，默认高亮「No」；
- 匹配大小写不敏感；词条首尾为字母或数字时要求单词边界（`sudo` 不命中 `sudoku`，`| sh` 不命中 `| shasum`）；
- `read_file` / `write_file` / `edit_file` 的路径解析后落在工作目录之外时同样需要确认；
- 另注册了 `tool_call_log_hook`（调用日志）与 `post_tool_call` 的 `large_tool_output_hook`（超长输出告警）。

## 子代理 / 团队 / Worktree

- **子代理**：`spawn_subagent` 用 `agent_type="sub-agent"` 单独组装提示词与工具池（后台运行，最多 30 轮），
  全程不进主会话历史，只回传最后一条文本结论。
- **队友**：`spawn_teammate` 起独立线程跑自己的模型循环（工具集 = 基础工具 + 任务认领/完成 + 消息 + `submit_plan`，
  `check_inbox` 在队友线程内被排除、由邮箱轮询代替），
  空闲时轮询邮箱与未认领任务（5s 一次，60s 超时退出）；`submit_plan` 会关闭审批闸门，
  在收到 `plan_approval_response` 前不再继续执行。
- **任务板**：任务以 JSON 落盘，`blockedBy` 构成依赖；队友认领带 worktree 的任务后，
  文件类工具自动切换到该 worktree 目录。
- **Worktree**：名称白名单校验（字母数字 `._-`，≤64 字符），删除前用 `git status --porcelain`
  与 `@{push}..HEAD` 检查未提交/未推送内容，避免误丢工作。

## MCP 接入

编辑 `.harness/.mcp/.mcp.json`：

```json
{
  "mcpServers": {
    "weather": { "command": "uv", "args": ["run", "stdio_server.py"] },
    "fetch": { "type": "streamable_http", "url": "https://example.com/mcp" },
    "bing": { "type": "sse", "url": "https://example.com/sse" }
  }
}
```

支持 stdio / sse / streamable_http；配置变化在下次获取时自动重建连接。
所有 MCP 会话归属独立后台事件循环，工具调用经 `run_coroutine_threadsafe` 调度回去执行（可被 Esc 取消）。
启动时 `main.py` 预热连接；若建连未完成，本次回合回退为纯内置工具池，
且每个 30s 窗口最多阻塞等待 5s，不会每轮都被拖住。

## Skills

在 `.harness/skills/<name>/SKILL.md` 放置技能，YAML frontmatter 提供 `name` / `description`：

```markdown
---
name: my-skill
description: 一句话说明这个技能做什么
---
技能正文，`load_skill` 会原样返回给模型。
```

扫描发生在模块导入时，技能摘要进入系统提示词的 Available skills 段，`/skills` 弹窗可直接选中触发一轮对话。

## Memory

`save_memory` 把记忆写为 `.harness/.memory/memory-<id>.json`，
标题与内容会在每轮构建系统提示词时注入 Memories 段，因此适合沉淀用户偏好与项目约定。

## 已知边界

- 同一轮内的多个工具调用目前串行执行（代码中留有 TaskGroup 并发化的 TODO）；
- 工具结果仅取文本内容，图片/多模态读取未实现；
- 权限策略为进程内 hook，`.hooks.json` 尚未被代码读取；
- `request_shutdown` / `request_plan` / `review_plan` 的审批闭环可跑通，但异常路径仍在打磨。
