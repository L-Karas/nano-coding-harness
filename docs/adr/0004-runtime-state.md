# 右栏运行状态收敛到 RuntimeState

## Status

accepted

右栏展示的运行状态（Todos、后台任务、子代理）与其结果存储收敛到 `core/runtime_state.py` 的 `RuntimeState` 单例：读写共用一把 `threading.Lock`，写侧只有具名入口（`set_todos` / `register_background` / `complete_background` / `pop_completed_backgrounds` / 子代理增删改），读侧只有类型化快照 `snapshot()`。`ACTIVATE_TEAMMATES` 是队友域状态（判重/清理），留在原处。

## Considered Options

- **单状态 module + 单锁 + 类型化快照**（采纳）：锁与 schema 的知识只存在一处，写入方与面板不再互相知道对方的结构。
- **每类各自加锁、面板聚合**（拒绝）：面板仍要知道哪类需要锁、哪类不需要。
- **保持模块级全局 + 只加快照函数**（拒绝）：写入面仍是多个全局，`CURRENT_TODOS` 的引用替换问题照旧。
