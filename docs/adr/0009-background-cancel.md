# 后台运行的跨回合取消归 RuntimeState

## Status

accepted

后台任务与子代理的取消新增一条不依附回合的通道：`RuntimeState` 增加一张
`work_id → 线程安全回调` 的取消句柄表（`attach_cancel` / `cancel_work`），条目快照带
`cancellable` 标志，`complete_background` / `remove_subagent` / `clear` 一并撤掉句柄。句柄由
发起者构造：`background_task.threadsafe_cancel(task)` 把 `task.cancel()` 经
`loop.call_soon_threadsafe` 转投到 TurnRunner 的 loop，`spawn_subagent` 给
`asyncio.current_task()` 挂同一形式的句柄，于是右栏点条目行尾 ■ 即可指名停单个后台任务 /
子代理：句柄跨回合有效，回合结束后仍能停；Esc 走原有路径取消当前回合的 ctx/task（含本回合
启动的后台工作），两条通道互不依赖。

取消是可辨认的结局：`ToolResult.is_cancelled` 单独成标志（不是 `is_error`），
`collect_background_results` 输出 `<status>cancelled</status>`，结果经原有通知通道在下一回合 /
auto_loop 注入。RuntimeState 继续保持叶子：不 import asyncio，句柄契约 = 任何线程可调、
只影响指名工作、投递失败（如 loop 已关闭）由 `cancel_work` 吞成 `False`。

worker 线程路径（纯同步工具）没有可杀句柄，条目不给 ■——当前实际会转后台的 `terminal` 与
`spawn_subagent` 都是原生异步，不受影响；模型侧取消工具不在本轮。

## Considered Options

- **RuntimeState 持句柄 + 发起者造线程安全回调**（采纳）：id 与条目已收在这里，面板读写同一处；
  asyncio / 线程知识留在发起者，state 保持叶子。
- **TurnRunner 持句柄**（拒绝）：TurnRunner 只管当前回合的 ctx/task，后台运行跨回合存活，
  登记面会与 RuntimeState 的 id / 条目分叉。
- **直接存 asyncio.Task**（拒绝）：state 被迫理解 loop，且跨线程 `task.cancel()` 不安全。
- **只留 Esc、不建指名通道**（拒绝）：Esc 够不到回合结束后仍在跑的工作，也无法只停单条。
