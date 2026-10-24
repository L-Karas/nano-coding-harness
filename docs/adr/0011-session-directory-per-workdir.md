# 会话目录按工作目录隔离

## Status

accepted

`SessionManager` 的会话文件与 `session_index.jsonl` 不再直接放在 `.harness/.session/`，而是放在
`.harness/.session/<格式化工作目录>/`。格式化规则：工作目录绝对路径的分隔符（`\` / `/`）与盘符
冒号 `:` 替换为 `_`，空格替换为 `-`；例如 `E:\AI-Programs\nano-harness` →
`E__AI-Programs_nano-harness`。`SESSION_ROOT_DIR` 保留 `.harness/.session` 作为根，`SESSION_DIR`
指向当前工作目录的子目录，`SESSION_INDEX_FILE` 跟随到该子目录。

已知限制：转义映射有损，`E:\a_b` 与 `E:\a/b` 会落到同一目录（会话与索引共享）；完整路径被压成
单段目录名，工作目录过深时可能超过文件系统 255 字节的单个文件名限制。子代理的
`working_directory` 会话仍归入主 `WORKDIR` 的会话目录，不按子代理目录拆分。旧版扁平的
`.harness/.session/` 会话不做自动迁移，旧文件保留在原处。

## Considered Options

- **按工作目录分目录保存**（采纳）：不同工作目录的会话分开存放，索引也各自独立；代价是转义
  有损与长路径限制。
- **继续扁平保存 + 索引里记录工作目录**（拒绝）：文件仍混在一起，清理 / 备份无法按目录操作，
  索引需要额外迁移逻辑。
- **用 hash 生成目录名**（拒绝）：避免碰撞与长路径，但目录名不可读，也偏离「格式化绝对路径」
  的明确命名约定。
- **自动迁移旧会话**（拒绝）：个人项目不需要为兼容旧布局保留迁移逻辑；旧文件留在原处，
  不做静默移动。
