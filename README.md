# nano-harness

一个用 Python 3.13 + Textual 写的 coding agent harness：全屏 TUI 对话、流式工具调用、
分层上下文压缩、后台任务、cron 调度，以及子代理 / 自治队友 / 任务板 / git worktree 隔离。
源码即文档，全部实现位于 `core/`。

## 核心特性

- **全屏 TUI**：卡片式聊天记录、流式思考/正文渲染、工具调用与 diff 预览，右栏展示 Todos、后台任务与子代理；
- **工具系统**：文件读写编辑、shell（慢命令自动转后台）、glob / grep、clarify、MCP 工具合并；
- **两层上下文压缩**：工具输出统一截断 + 历史摘要，阈值随当前模型动态解析；
- **健壮性**：Esc 中断、按 provider 分类的错误退避重试、截断自动扩窗与续写恢复、会话逐条落盘；
- **多代理协作**：子代理、自治队友、任务板依赖、JSONL 邮箱与计划审批、git worktree 隔离；
- **可扩展**：权限 Hook（危险命令二次确认）+ `.harness/extensions/` 外部扩展四个事件介入。

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
- `/model`：切换模型（写入 `.harness/.settings.json`）
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

内置指令：`/new`（新会话）、`/sessions`（会话选择）、`/compact`（压缩当前上下文，可被 Esc 中断）、
`/fork`（列出当前会话的用户消息，选中后从该消息前分叉出新会话并重放历史，消息原文填回输入栏供修改）、
`/skills`（技能列表，选中即作为一轮对话发出）、`/mcp`（MCP server 名列表，Enter 查看该 server
的工具列表，`Insert` 弹出 JSON 配置窗、`Ctrl+S` 落盘，`Delete` 删除 server）、
`/provider`、`/model`、`/effort`、`/settings`（编辑 AgentConfig 运行参数）、
`/login`（别名 `/logout`，注册/注销自定义提供方与模型）、`/exit`（别名 `/quit`）。

## 配置目录

首次启动由 `core.bootstrap.bootstrap()` 自动创建 `.harness/`（配置、会话、技能、任务、日志等，
幂等且线程安全）。完整目录清单与运行参数见 [`docs/configuration.md`](docs/configuration.md)。

## 扩展系统

外部扩展放在 `.harness/extensions/<name>/`，不改 harness 源码即可在 LLM 调用前/后、工具调用前/后
介入主流程。加载规则、事件语义、实战配方与排错见 [`docs/extensions.md`](docs/extensions.md)。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | core/ 模块地图、Agent 主循环、两层上下文压缩、健壮性设计 |
| [`docs/tools.md`](docs/tools.md) | 工具系统与内置工具清单、后台任务 |
| [`docs/tui.md`](docs/tui.md) | TUI 布局与界面模块 |
| [`docs/configuration.md`](docs/configuration.md) | 模型注册表、`.harness/` 目录、压缩与运行参数 |
| [`docs/features.md`](docs/features.md) | MCP、Skills、Memory、Cron 调度 |
| [`docs/permissions.md`](docs/permissions.md) | 权限与 Hook 策略 |
| [`docs/teams.md`](docs/teams.md) | 子代理 / 队友 / 任务板 / Worktree |
| [`docs/extensions.md`](docs/extensions.md) | 外部扩展系统完整指南 |
| [`examples/`](examples/) | s01–s18 教学脚本（从 agent loop 到 worktree 隔离的演进示例）与扩展示例（`extension_example`、`jev-for-web-search`） |
