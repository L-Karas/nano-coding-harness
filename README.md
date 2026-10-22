# nano-harness

一个用 Python 3.13 + Textual 写的 coding agent harness：全屏 TUI 对话、流式工具调用、
分层上下文压缩、后台任务、cron 调度与子代理。
源码即文档，主体实现位于 `core/`；入口 `main.py` 与日志 `logger.py` 在根目录。

## 核心特性

- **全屏 TUI**：卡片式聊天记录、流式思考/正文渲染、工具调用与 diff 预览，右栏展示 Todos、后台任务与子代理；
- **工具系统**：文件读写编辑（`read_file` 支持 png/jpg/jpeg/gif/webp 本地图片，单图 ≤ 5 MB、超限自动缩小，以及图片 URL；图片经 user 消息注入视觉模型）、shell（慢命令自动转后台）、glob / grep、clarify、MCP 工具合并；
- **两层上下文压缩**：工具输出统一截断 + 历史摘要，阈值随当前模型动态解析；
- **健壮性**：Esc 中断、按 provider 分类的错误退避重试、截断自动扩窗与续写恢复、会话逐条落盘；
- **子代理**：独立提示词与工具池，后台运行、最多 30 轮，只回传最终文本结论；
- **可扩展**：`.harness/.permissions.json` 权限规则（allow / ask / deny）+ `.harness/extensions/` 外部扩展五个事件介入（`before_tool` 决定票与内置权限门）。

## 快速开始

```bash
uv sync          # 安装依赖（requires-python >= 3.13）
uv run main.py   # 启动 Textual TUI
```

首次启动会在项目根自动创建 `.harness/` 配置与数据目录（见「配置目录」）。
模型未配置时界面可正常打开，先用 `/provider` 填 API Key，再 `/model` 选模型即可对话。

```bash
uv run pytest tests/                          # 单元测试
uv run python -m core.tui.ui_textual --smoke  # TUI 无头冒烟自检（渲染/流式/补全/弹窗/权限）
```

## 模型配置

内置 Deepseek、Qwen、Kimi、Z.AI 等 provider（`core/client/models.json`），也可注册自定义提供方；
模型的视觉 / 思考档位声明与注册表细节见 [`docs/configuration.md`](docs/configuration.md)。

- `/provider`：填/删 API Key（写入 `.harness/.auth.json`）
- `/model`：从已配置 provider 的模型列表中切换当前会话模型（只改内存 client，不落盘；默认模型在 `/settings` 修改）
- `/effort`：切换思考档位（`minimal` / `low` / `medium` / `high` / `max`）
- `/login`（别名 `/logout`）：注册 / 注销自定义提供方与模型

## TUI 使用

左右分栏：左侧会话（卡片式聊天记录 + 状态行 + 输入条 + 页脚），右侧 Information 面板
（Todos / 后台任务 / 子代理，默认折叠，有活动时自动展开，可点击折叠）。
布局与界面模块见 [`docs/tui.md`](docs/tui.md)。

| 操作 | 说明 |
| --- | --- |
| `Enter` | 无候选时发送消息；有 `/` 指令候选时先补全再发送 |
| `Shift+Enter` / `Ctrl+J` / `Ctrl+Enter` | 输入换行（`Tab` 不缩进，直接被吞掉） |
| `/` | 弹出指令候选，`Tab`/`Enter` 补全，`↑`/`↓` 选择 |
| `@` | 弹出项目文件候选（相对路径，补进输入，不发送） |
| `Esc` | 收起候选；权限确认挂起时「拒绝」；否则中断当前回合或压缩 |
| `Ctrl+C` | 不退出（有选中文本时复制）；退出用 `/exit` 或 `Ctrl+Q` |
| `Ctrl+←` / `Ctrl+→` | 调宽信息栏（20%–40%）；折叠态 `Ctrl+←` 展开，`Ctrl+→` 收到底即折叠 |
| 鼠标 | 多数弹窗列表单击行 = `Enter` 执行（注销窗点击仅选中）；聊天卡片、信息栏分区 / 条目可点击；运行中的后台任务 / 子代理条目行尾 `■` = 指名停止（句柄跨回合有效，见 [ADR-0009](docs/adr/0009-background-cancel.md)）；`/settings` 点开关 / 下拉选项即落盘、点输入框进入编辑态 |

内置指令：`/new`（新会话）、`/sessions`（会话选择）、`/compact`（压缩当前上下文，可被 Esc 中断）、
`/fork`（列出当前会话的用户消息，选中后从该消息前分叉出新会话并重放历史，消息原文填回输入栏供修改）、
`/skills`（技能列表，选中即作为一轮对话发出）、`/mcp`（MCP server 名列表，Enter 查看该 server
的工具列表，`Insert` 弹出 JSON 配置窗、`Ctrl+S` 落盘，`Delete` 删除 server）、
`/provider`、`/model`、`/effort`、`/settings`（编辑 AgentConfig 运行参数）、
`/login`（别名 `/logout`，注册/注销自定义提供方与模型）、`/exit`（别名 `/quit`）。

## 配置目录

首次启动由 `core.bootstrap.bootstrap()` 自动创建 `.harness/`（配置、会话、技能、任务、日志等，
幂等且线程安全）。完整目录清单与运行参数见 [`docs/configuration.md`](docs/configuration.md)。

## 权限策略

工具调用先过内置权限门（`before_tool` 的 gate 阶段）：硬编码地板永远拒绝（`sudo` 等），
用户可在 `.harness/.permissions.json` 写规则（terminal 命令正则与外部目录策略 / 文件工具
path 正则与越界策略）表达 allow / ask / deny；外部扩展也能用 `decide()` 投票，冲突时弹确认卡。
判定顺序、规则格式与已知风险见 [`docs/permissions.md`](docs/permissions.md)。

## 扩展系统

外部扩展放在 `.harness/extensions/<name>/`，不改 harness 源码即可在 LLM 调用前/后、工具调用前/后
介入主流程，或用 `tool_execute` 接管内置工具执行。加载规则、事件语义、实战配方与排错见
[`docs/extensions.md`](docs/extensions.md)。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | core/ 模块地图、Agent 主循环、两层上下文压缩、健壮性设计 |
| [`docs/tools.md`](docs/tools.md) | 工具系统与内置工具清单、后台任务 |
| [`docs/tui.md`](docs/tui.md) | TUI 布局与界面模块 |
| [`docs/configuration.md`](docs/configuration.md) | 模型注册表、`.harness/` 目录、`/settings` 参数与压缩运行参数 |
| [`docs/context-compaction.md`](docs/context-compaction.md) | 两层上下文压缩：触发 / 保留预算、切分与摘要、模型切换行为 |
| [`docs/features.md`](docs/features.md) | MCP、Skills、Memory、Cron 调度 |
| [`docs/permissions.md`](docs/permissions.md) | 权限策略：三层判定、规则文件、扩展决定票与风险 |
| [`docs/teams.md`](docs/teams.md) | 子代理：独立提示词、工具池与轮次上限 |
| [`docs/extensions.md`](docs/extensions.md) | 外部扩展系统完整指南 |
| [`examples/`](examples/) | 配置示例 `permission_config_example.json`、扩展示例（`extension_example`、`jev-for-web-search`、`jev-for-terminal`）与 `tutorials/` 下的 s01–s18 教学脚本（agent loop 演进示例；其中 s12 / s15–s18 的队友 / 任务板 / worktree 为独立原型，未进入 `core/`） |
