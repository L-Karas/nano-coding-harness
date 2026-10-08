# 子代理 / 团队 / Worktree

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
