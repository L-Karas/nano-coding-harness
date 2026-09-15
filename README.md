## 环境搭建

```bash
uv sync
```

## 模型配置

模型与提供商配置统一存放在 `.harness/` 目录下（首次启动自动创建），
主循环、子代理、上下文压缩、teammate 等所有 LLM 调用共用同一份配置
（`core.model` 的 `shared_model_client` / `get_model_client`）：

- `.harness/.auth.json` — 各 provider 的 api_key（`configure_provider` 写入）
- `.harness/.setting.json` — `default_provider` / `default_model` / `default_thinking_level`（`set_model_client` 写入）

provider 及可用模型清单见 `core/model/models.json`。

## 测试

```bash
uv run main.py
```

## 接入 MCP Server

参考 `.mcp/.mcp.json` 内容修改、配置MCP Server，harness将自动连接配置的MCP Server。

## 接入 Skills

将 `skill` 放入 `.skill` 文件夹中即可自动加载。

## 功能支持

### 功能

| 功能                       | 备注 |  |
|--------------------------|----|--|
| 基础工具bash、read、edit、write |    |  |
| 复杂任务任务分解 todowrite       |    |  |
| 时间调度cron                 |    |  |
| 子代理sub-agent             |    |  |
| 权限控制permission           |    |  |
| 接入skill                  |    |  |
| 接入mcp server             |    |  |
| 上下文压缩                    |    |  |
| 长耗时任务后台运行                |    |  |
| 子代理团队teammate、worktree   |    |  |
|                          |    |  |
|                          |    |  |

### 支持基础工具

| 工具               | 功能                 | 备注  |
|------------------|--------------------|-----|
| bash             | 执行 bash 命令         |     |
| read_file        | 读取文件               |     |
| write_file       | 写入文件               |     |
| edit_file        | 查找并替换文件内容          |     |
| glob             | 按 glob 模式查找文件      |     |
| grep             | 搜索文件内容             |     |
| todo_write       | 创建和管理任务列表          |     |
| task             | 启动子代理,返回最终摘要       |     |
| load_skill       | 按名称加载技能            |     |
| compact          | 压缩上下文后继续对话         |     |
| create_task      | 创建任务               |     |
| list_tasks       | 列出所有任务             |     |
| get_task         | 获取任务详情             |     |
| claim_task       | 认领任务               |     |
| complete_task    | 完成任务               |     |
| schedule_cron    | 调度 cron 任务         |     |
| list_crons       | 列出所有 cron 任务       |     |
| cancel_cron      | 取消 cron 任务         |     |
| spawn_teammate   | 启动协作者代理            |     |
| send_message     | 向其他代理发送消息          |     |
| check_inbox      | 检查收件箱              |     |
| request_shutdown | 请求协作者关闭            | 待完善 |
| request_plan     | 请求协作者提交计划          | 待完善 |
| review_plan      | 审批或驳回计划            | 待完善 |
| create_worktree  | 创建隔离的 git worktree |     |
| remove_worktree  | 删除 worktree        |     |
| keep_worktree    | 保留 worktree 供人工审查  |     |
| connect_mcp      | 连接 MCP 服务器并发现工具    |     |
