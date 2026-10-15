# 子代理

- **子代理**：`spawn_subagent` 用 `agent_type="sub-agent"` 单独组装提示词与工具池（后台运行，最多 30 轮），
  每步经共享 `core/agent_step.py` 执行；全程不进主会话历史，只回传最后一条文本结论。
- **模型**：子代理默认模型取 `default_sub_model`（回退 `default_model`），思考档位取
  `default_sub_model_thinking_level`；上下文压缩走 `prepare_messages(history, ctx, sub_model=True)`，
  预算与摘要模型随子模型（见 [`context-compaction.md`](context-compaction.md)）。
- **可见性**：工具池按 `agent_type` 过滤（`spawn_subagent` / `save_memory` / `load_skill` 等 main-only 工具
  对子代理不可见）；子代理的增删改经 `RuntimeState` 反映到右栏 Information 面板。
- **工作目录与展示**：`working_directory` 指定子代理工作目录（缺省与主代理一致），随 `ToolContext.cwd`
  进入子代理的工具与系统提示词；`goal` 为右栏 Subagents 条目展开时展示的简短目标。
- **取消**：运行中的子代理在右栏 Subagents 条目行尾给 `■`，点击指名停止
  （`RuntimeState.cancel_work`，句柄跨回合有效，见 [ADR-0009](adr/0009-background-cancel.md)）；
  停下的结论以 `<status>cancelled</status>` 的后台通知回传。
