# SessionManager 是消息状态的唯一 owner

## Status

accepted

`Message` 是会话语义的类型：`SessionManager` 的 interface（`load_messages` / `update_messages` / `add_message`）以 `Message` 为货币，JSON 只出现在文件边界。`current_session` 是只读 property，`reset()` 是唯一的「清空当前会话」入口，`load_messages` 不再有创建会话的写副作用。UI 的 fork 预览宽度由 TUI 自持，存储只保留自己的标题截断长度。

## Considered Options

- **Message 贯穿 session interface**（采纳）：dict round-trip 只留在文件序列化处；map / 指针 / 文件三者的不变量收进 SessionManager。
- **只换 load/update 类型、其余原地转换**（拒绝）：round-trip 仍在调用点。
- **全量 Message 化（含扩展注入与 StepOutcome）**（拒绝）：破坏扩展公共契约，收益不覆盖成本。
