# 工具系统

工具即 `BaseTool` 子类（pydantic 模型）：类名 snake_case 即工具名，docstring 即工具描述，
字段即 JSON Schema；`agent_type` 集合决定该工具对哪些代理可见。
执行入口是类上的 `run` / `arun` 方法：schema 字段即参数（`self.*`），覆写其一即可
（`arun` 缺省做取消检查后调 `run`，`run` 缺省用 `asyncio.run` 跑 `arun`）。新增工具只需在对应目录
定义类，并在 `base_tools/__init__.py`、`extra_tools/__init__.py` 或 `web_search/__init__.py` 导出。

运行期由 `assemble_tool_pool()` 按 `agent_type` / `experimental` 装配成 `ToolPool`：向模型提供 schema
列表、按名执行，并以 `ToolResult` 承载成功 / 失败；MCP 工具在装配处包成同一条目。

## 基础工具（main / sub-agent / teammate）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `terminal` | `command`, `should_run_in_background` | shell 执行（同步路径 120s 超时），输出截断 5 万字符；`should_run_in_background=true` 或命中慢命令启发式时自动转后台 |
| `read_file` | `path`, `limit=2000`, `offset=1` | 分页读取文本（默认 2000 行 / 50KB，任一先到即停），超限时返回续读 offset 提示 |
| `write_file` | `path`, `content` | 写文件，执行前渲染 diff 预览 |
| `edit_file` | `path`, `old_text`, `new_text` | 单次精确替换，执行前渲染 diff 预览 |
| `glob` | `pattern` | 按 glob 模式查找文件（异步路径优先 ripgrep 并尊重 .gitignore，缺失时回退 Python glob） |
| `grep` | `pattern`, `path`, `file_pattern` | 优先 ripgrep / grep，缺失时回退纯 Python 实现 |
| `clarify` | `options`, `multi_select=false` | 停靠区选项列表询问用户（main / sub-agent）：单选或 Space 多选 + Enter 确认，末尾 Other 可键入自定义回答，Esc 取消 |
| `web_search` | `query`, `max_results=10` | 联网搜索：按已配置 Key 的提供方顺序（firecrawl → tavily → exa）尝试，失败自动禁用该提供方，最终回退免 Key 的 ddgs；`max_results` 最小 1 |
| `web_extract` | `url` | 抓取网页正文：先 `npx defuddle` 解析，再按同一提供方顺序回退，最终 ddgs；结果截断 15000 字符 |

工具经 `ToolContext` 拿到取消信号、工作目录与发起者身份（`run` / `arun` 的 `tctx` 参数）；
teammate 认领带 worktree 的任务后，`claim_task` 更新上下文的 `cwd`，后续文件工具随之切换目录。

## 扩展工具

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

`should_run_background()` 据此判定：`terminal` 显式传 `should_run_in_background=true`，
或命令命中慢命令启发式（`make` / `pytest` / `sleep` / `pip install` / `npm test` / `cargo build` 等，
只看每条子命令的命令词，宁可漏判不误判）。

命中后立即返回 `[Background task <bg-id> started]` 占位结果，工具在守护线程中继续执行；
下一轮开始时结果以 `<background-task-notification>` 注入会话，右栏 Background Tasks 同步显示状态。
