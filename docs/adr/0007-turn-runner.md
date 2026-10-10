# Turn 的线程契约收进 TurnRunner

## Status

accepted

`core/turn_runtime.py` 的 `TurnRunner` 独占三样东西：一个私有事件循环 + daemon 线程（模型客户端绑定该 loop）、一把 `threading.Lock`（用户回合、cron 自动回合、`/compact` 串行）、当前回合的 `AgentRunContext` 与 `asyncio.Task`。`AgentRuntime` 组合它，对外只暴露 `run_turn(prepare=...)` / `run_compact()` / `interrupt()` / `is_running()` / `current_context`；`AGENT_LOCK` 全局与调用方对 loop / 锁 / 私有任务的知识一并消失。`run_turn` 的 `prepare` 在锁内执行，承担 auto_loop 的「消费 cron + 注入后台结果，无活则跳过」判定；回合内重入（loop 线程再次 `run`）直接抛 `RuntimeError`。MCP 自己的事件循环不在本轮范围。

## Considered Options

- **TurnRunner 组合**（采纳）：loop、锁、ctx/task 配对收进一个 implementation，调用方只提意图。
- **只把 AGENT_LOCK 搬进 AgentRuntime**（拒绝）：loop 与任务登记仍以私有属性泄给调用方与测试。
- **通用 LoopThread 连 MCP 一起复用**（拒绝）：把长生命周期会话循环的关闭 / 取消语义卷进本轮，风险不匹配收益。
