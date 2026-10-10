# Agent step 是共享单元

## Status

accepted

一次模型调用及其触发的工具派发共同构成 **Agent step**（`core/agent_step.py` 的 `run_agent_step()`），main / sub-agent / teammate 共用同一实现；三方差异经 `StepPolicy`（扩展、permission hooks、后台路由、diff 预览、length 时延迟执行工具、halt 回调）与 `StepRenderer` 显式化。step 不改调用方的 messages、不碰 session/cron/阶段标记/协议门控，这些骨架留在各自 loop。

## Considered Options

- **共享 step**（采纳）：LLM 调用链 + 工具派发链整体收敛，三方差异成为显式策略。
- **只共享工具派发**（拒绝）：模型调用段仍是三份，行为分歧照旧。
- **完整 AgentLoop 泛化**（拒绝）：把 session / cron / 阶段 / 协议门控塞进参数，外层差异被硬抽象。
