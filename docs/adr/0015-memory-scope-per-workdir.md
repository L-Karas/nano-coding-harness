# 记忆按工作目录 / 全局二分保存

## Status

accepted

`Memory` 新增 `scope: Literal["local", "global"]` 字段，默认 `local`。存储位置由 scope 决定：

- `local`：`.nano-harness/.memory/<格式化工作目录>/memory-<id>.json`，工作目录名复用会话目录的
  `format_workdir_name`（如 `E:\AI-Programs\nano-harness` → `E__AI-Programs_nano-harness`）。
- `global`：`.nano-harness/.memory/global-memories/memory-<id>.json`，跨工作目录共享。

`scope` 由所在目录决定，不写入记忆 JSON（保存时剔除）。`save_memory` 的 `scope`
可选、默认 `local`。系统提示词同时注入 global 与当前 local（global 在前，同 id 时 local 覆盖并
记 warning），表格增加 Scope 列。`load_memory(id)` / `delete_memory(id)` 按 local → global
顺序查找，不要求调用方传 scope；同 id 两处都有时，删除会一并清掉两份，避免下次加载又出现。

旧版扁平 `.nano-harness/.memory/*.json` 不做代码内迁移；本次落地时已手工移入 `global-memories/`
（旧语义等同于全局共享），个人项目不为此保留启动期迁移逻辑。

已知限制：工作目录名转义有损、超长路径受单段文件名限制，与
[ADR-0011](0011-session-directory-per-workdir.md) 相同；子代理的 `working_directory` 不改变
记忆目录，仍按主 `WORKDIR` 归入 local。

## Considered Options

- **显式 scope 字段 + 两类目录**（采纳）：范围与 `mem_type` 正交，模型保存时显式选择；
  代价是新增一个参数与提示词中的一列。
- **由 `mem_type` 推导**（拒绝）：`project` → local、其余 → global 的映射并不成立
  （`user` 偏好也可能只对当前项目成立），且把内容分类耦合进存储位置。
- **继续扁平保存**（拒绝）：不同工作目录的记忆无法区分清理 / 备份，也无法表达共享语义。
- **scope 写进 JSON**（拒绝）：目录已经是唯一事实来源，手工搬文件后字段会与位置不一致。
- **自动迁移旧扁平文件**（拒绝）：个人项目、一次性变更，手工移动即可，不为兼容旧布局保留
  迁移代码。
