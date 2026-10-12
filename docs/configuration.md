# 配置

## 模型注册表

`core/client/models.json` 内置 provider 清单（Deepseek、Qwen、Kimi、Z.AI），
每个模型声明 `vision` / `thinking` / `thinking_level_map`（档位 → `reasoning_effort`），
`thinking_level_map` 中值为 `false` 表示该模型不支持此档位（此时不发送 `reasoning_effort`）。

- `/provider`：填/删 API Key（写入 `.harness/.auth.json`）
- `/model`：从已配置 provider 的模型列表中切换当前会话的模型（只改内存 client，不落盘；默认值需到 `/settings` 修改）
- `/effort`：切换思考档位（`minimal` / `low` / `medium` / `high` / `max`）
- `/login`（别名 `/logout`）：注册 / 注销自定义提供方与模型（写入 `.harness/.custom_providers.json` /
  `.harness/.custom_models.json`）

所有 LLM 调用共用单例 `core.client.shared_model_client()`：主循环、子代理、上下文压缩走同一份配置，
不存在第二处模型来源。

## `.harness/` 配置与数据目录

启动时由 `core.bootstrap.bootstrap()` 自动创建
（`main.py` / TUI `run()` / `start_agent_runtime()` 均会调用；单独 import `core.*` 不再产生文件副作用）：

| 路径 | 内容 |
| --- | --- |
| `.harness/.settings.json` | `AgentConfig` 参数（默认模型与思考档位、压缩与运行参数）：由 `/settings` 写入；`/model` / `/effort` 只切换当前会话的内存 client，不写盘；注销 provider / model 会清空指向它的默认模型引用 |
| `.harness/.auth.json` | 各 provider 的 `api_key` |
| `.harness/.mcp/.mcp.json` | MCP Server 配置（`mcpServers`） |
| `~/.agents/skills/<name>/SKILL.md` / `.harness/skills/<name>/SKILL.md` / `.agents/skills/<name>/SKILL.md` | 技能清单三来源（global / user / project，同名 project > user > global；YAML frontmatter + 正文） |
| `.harness/.memory/*.json` | 长期记忆条目 |
| `.harness/.session/` | `session-<ts>.jsonl` 会话文件 + `session_index.jsonl` 索引 |
| `.harness/.scheduled_tasks.json` | 持久化 cron 任务 |
| `.harness/.task_outputs/tool_results/` | 超长工具输出落盘文件 |
| `.harness/log/<module>.log` | 各模块日志 |

## `/settings` 参数一览

`/settings` 弹窗编辑 `.harness/.settings.json` 的 `AgentConfig` 参数。交互：↑/↓ 或 Tab 切字段，
Enter 进入编辑态、再次 Enter 校验全部字段并落盘；开关 Enter 或点击直接切换并落盘；Esc 关窗
（未保存的改动丢弃）。数字字段留空 = 回退 `AgentConfig` 默认值（没有“未设置”语义）；
未在表单中的字段（`max_retries`、`max_recovery_retries`、`base_retry_delay_ms`、`compact_type`）
保存时原样保留。下表「窗口显示名」是窗口左侧的短语，「字段」是落盘键名。

### 模型与思考

| 窗口显示名 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| Default model | `default_model` | 空 | 主 client 的默认模型（`provider:model`）；空 = 未选择。`/model` 只切换当前会话的内存 client，默认值在此窗口修改才会落盘 |
| Default sub model | `default_sub_model` | 空 | 子代理（sub-agent）默认模型；空则回退 `default_model`；支持跨 provider |
| Default fallback model | `default_fallback_model` | 空 | 预留字段：模型不可用时自动切换的能力尚未接入；目前只在注销 provider / model 时被清空（见 `core/recovery/error_recovery.py` 备注） |
| Default thinking level | `default_thinking_level` | `max` | 主模型默认思考档位（minimal / low / medium / high / max），按模型 `thinking_level_map` 映射为 `reasoning_effort`；该档位不被模型支持时不发送 |
| Sub model thinking level | `default_sub_model_thinking_level` | `max` | 子模型思考档位；未设置 `default_sub_model` 时回退 `default_thinking_level` |

### 输出 token

| 窗口显示名 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| Default max tokens | `default_max_tokens` | `16000` | 主 / 子代理每次模型调用的默认 `max_tokens`；经当前模型 `max_output` 钳制 |
| Escalated max tokens | `escalated_max_tokens` | `32000` | `finish_reason == "length"` 后升级重试一次用的上限；模型未声明 `max_output` 时，也是压缩阈值预留的输出余量（`output_reserve_tokens`） |
| Summary max tokens | `summary_max_tokens` | `20000` | 生成摘要时的最大 token 生成数；经当前模型 `max_output` 钳制 |

### 上下文压缩

| 窗口显示名 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| Auto compact context | `auto_compact` | 开 | 每回合 `prepare_messages` 是否做摘要压缩；关闭时只截断超长工具输出，超长历史可能由 API 报 context 超限 |
| Compact trigger tokens | `compact_trigger_tokens` | `500000` | 历史估算 token 超过它触发压缩；按当前模型窗口封顶（`min(值, 上下文 − 输出余量)`），无可用窗口时为 0（不压缩） |
| Compact reserve tokens | `compact_reserve_tokens` | `50000` | 自动压缩后保留的最近消息 token 预算；封顶有效触发阈值 |

触发判断、切分规则与手动 `/compact` 的细节见 [`context-compaction.md`](context-compaction.md)。

### 功能开关

| 窗口显示名 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| Enable experimental tools | `enable_experimental_tools` | 关 | 放开标注 `experimental` 的工具，预留给后续实验性工具：主 / 子代理装配把它传入 `assemble_tool_pool(enable_experimental=…)`，按 `BaseTool.experimental` 过滤；当前内置工具均未标注，开关开 / 关结果相同 |
| Enable deferred tool loading | `enable_defer_tools` | 关 | 预留开关：按需（延迟）加载工具 schema；当前无实现，暂不生效 |
| Enable deferred skill loading | `enable_defer_skills` | 关 | 预留开关：按需（延迟）加载技能清单；当前无实现，暂不生效 |

## 上下文压缩参数

`compact_trigger_tokens` / `compact_reserve_tokens` / `summary_max_tokens` 等来自 `AgentConfig`
（默认值见 `core/config.py`），均为绝对 token 数，可在 `/settings` 中调整；上下文长度与最大输出
直接取自当前模型的注册表配置（`core/client/models.json` / `.harness/.custom_models.json`），两个阈值
在运行中按当前模型窗口封顶，切换模型（`/model`）后立即生效。模型未声明最大输出时不做输出钳制，
压缩阈值改为给 `escalated_max_tokens` 留出余量（保证 输入 + 输出 < 上下文长度）。

两层管线、切分规则与手动 `/compact` 等细节见 [`context-compaction.md`](context-compaction.md)。
