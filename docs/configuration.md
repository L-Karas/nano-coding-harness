# 配置

## 模型注册表

`core/client/models.json` 内置 provider 清单（Deepseek、Qwen、Kimi、Z.AI），
每个模型声明 `vision` / `thinking` / `thinking_level_map`（档位 → `reasoning_effort`），
`thinking_level_map` 中值为 `false` 表示该模型不支持此档位（此时不发送 `reasoning_effort`）。

- `/provider`：填/删 API Key（写入 `.harness/.auth.json`）
- `/model`：从已配置 provider 的模型列表中切换（写入 `.harness/.settings.json`）
- `/effort`：切换思考档位（`minimal` / `low` / `medium` / `high` / `max`）
- `/login`（别名 `/logout`）：注册 / 注销自定义提供方与模型（写入 `.harness/.custom_providers.json` /
  `.harness/.custom_models.json`）

所有 LLM 调用共用单例 `core.client.shared_model_client()`：主循环、子代理、上下文压缩、
teammate 走同一份配置，不存在第二处模型来源。

## `.harness/` 配置与数据目录

启动时由 `core.bootstrap.bootstrap()` 自动创建
（`main.py` / TUI `run()` / `start_agent_runtime()` 均会调用；单独 import `core.*` 不再产生文件副作用）：

| 路径 | 内容 |
| --- | --- |
| `.harness/.settings.json` | `AgentConfig` 参数：默认 `provider` / `model` / `thinking_level` 由 `/provider`、`/model`、`/effort` 写入，其余运行参数由 `/settings` 写入 |
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

## 上下文压缩参数

`compact_threshold` / `reserve_threshold` / `summary_max_tokens` 等来自 `AgentConfig`（默认值见
`core/config.py`），可在 `/settings` 中调整；上下文长度与最大输出
直接取自当前模型的注册表配置（`core/client/models.json` / `.harness/.custom_models.json`），两个阈值
在运行中按当前模型动态解析，切换模型（`/model`）后立即生效。模型未声明最大输出时不做输出钳制，
压缩阈值改为给 `escalated_max_tokens` 留出余量（保证 输入 + 输出 < 上下文长度）。
