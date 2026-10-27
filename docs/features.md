# 内置扩展能力（MCP / Skills / Memory / Cron）

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
每个 server 可加 `"enabled": false` 表示禁用（缺省即启用）；禁用项启动时不建连、不贡献工具，
但在 `/mcp` 窗口保留条目，按 `Ctrl+E` 可运行时重新启用并后台重连。Insert 改连接参数不会
改变启用状态，Delete 才会彻底删除配置。失败或禁用的 server 按 Enter 只提示，不打开工具列表。
所有 MCP 会话归属独立后台事件循环，工具调用经 `run_coroutine_threadsafe` 调度回去执行（可被 Esc 取消）。
启动时 `main.py` 预热连接；若建连未完成，本次回合回退为纯内置工具池，
且每个 30s 窗口最多阻塞等待 5s，不会每轮都被拖住。

## Skills

技能从三处扫描，同名靠后来源覆盖靠前：global `~/.agents/skills/`、user `.harness/skills/`、project `<cwd>/.agents/skills/`（优先级 project > user > global）。每处一个 `<name>/SKILL.md`，YAML frontmatter 提供 `name` / `description`：

```markdown
---
name: my-skill
description: 一句话说明这个技能做什么
---
技能正文；`load_skill` 把它与 `SKILL.md` 路径一起包进 `<skill>` 标签返回给模型。
```

扫描在启动阶段（`core/bootstrap.py` 的 `bootstrap()`）执行一次，技能摘要进入系统提示词的 Available skills 段。
`/skills` 弹窗中 Enter/点击直接把技能作为一轮对话发出；Tab 把 `[Invoke skill '<name>']` 填入输入栏
并聚焦，用户可继续写补充消息后一起发送。方括号只包住调用本身，其后内容即补充信息；
技能调用视为用户消息，可作为会话标题并参与 `/fork`。
`Ctrl+E` 切换选中技能的启用状态，落盘到 `.harness/.skills.json`（`{"disabled": [name, ...]}`）：
禁用项从系统提示词表移除、`load_skill` 返回不可用，窗口中显示为红 `○`，可再次切换恢复。
扫描时会剔除已不存在的禁用名；状态文件缺失按全员启用处理，损坏时 fail-open（全员启用）并在
`/skills` 顶部显示一条只读故障行。

## Memory

`save_memory` 把记忆写为 `.harness/.memory/memory-<id>.json`，
标题与内容会在每轮构建系统提示词时注入 Memories 段，因此适合沉淀用户偏好与项目约定。

## Cron 调度

支持标准 5 段表达式：`*`、`*/N`、`a,b`、`a-b`，日期与星期同时限定时按 OR 语义匹配。
调度线程每秒匹配一次，命中的任务进入队列并记忆「分钟级」触发标记避免重复触发。
`durable=true` 的任务写入 `.harness/.scheduled_tasks.json` 并在进程启动时加载。
`auto_loop` 后台线程取出队列任务后，在 `AGENT_LOCK` 内直接跑一整轮 agent 回合。
