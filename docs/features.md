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

## Cron 调度

支持标准 5 段表达式：`*`、`*/N`、`a,b`、`a-b`，日期与星期同时限定时按 OR 语义匹配。
调度线程每秒匹配一次，命中的任务进入队列并记忆「分钟级」触发标记避免重复触发。
`durable=true` 的任务写入 `.harness/.scheduled_tasks.json` 并在进程启动时加载。
`auto_loop` 后台线程取出队列任务后，在 `AGENT_LOCK` 内直接跑一整轮 agent 回合。
