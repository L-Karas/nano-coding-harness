# 子代理

- **子代理**：`spawn_subagent` 用 `agent_type="sub-agent"` 单独组装提示词与工具池（后台运行，最多 500 轮），
  每步经共享 `core/agent_step.py` 执行；全程不进主会话历史，只回传最后一条文本结论。
- **模型**：子代理默认模型取 `default_sub_model`（回退 `default_model`），思考档位取
  `default_sub_model_thinking_level`。`spawn_subagent` 可传
  `model_choice: {model: "provider:model", thinking_level: "..."}` 为单次子代理显式指定模型
  （主代理可先用 `list_models` 查看可用清单）；两者必须成对给出，省略整个对象则沿用默认。
  client 按 `(provider, model, thinking_level)` 缓存并与共享单例分离，不同模型的子代理可并发；
  上下文压缩用该子代理自己的 client（预算与摘要同模型，见
  [ADR-0016](adr/0016-subagent-model-choice.md) 与 [`context-compaction.md`](context-compaction.md)）。
- **可见性**：工具池按 `agent_type` 过滤（`spawn_subagent` / `save_memory` / `load_skill` 等 main-only 工具
  对子代理不可见）；子代理的增删改经 `RuntimeState` 反映到右栏 Information 面板。
- **工作目录与展示**：`working_directory` 指定子代理工作目录（缺省与主代理一致），随 `ToolContext.cwd`
  进入子代理的工具与系统提示词；`role` 为右栏 Subagents 条目展示的角色标签（如 reviewer / researcher），
  替代内部 id 且仅影响展示；`goal` 为条目展开时展示的简短目标。
- **取消**：运行中的子代理在右栏 Subagents 条目行尾给 `■`，点击指名停止
  （`RuntimeState.cancel_work`，句柄跨回合有效，见 [ADR-0009](adr/0009-background-cancel.md)）；
  停下的结论以 `<status>cancelled</status>` 的后台通知回传。
