# `.nano-harness` 路径收敛到 PATHS 快照

## Status

accepted

`core/config.py` 的 18 个派生路径不再各是一个模块级常量，而是 `Paths` dataclass 的字段；
单例 `PATHS` 在导入期由 `Paths.resolve(DEFAULT_MODE)` 构造，`configure_mode(mode)` 用
`PATHS.replace_with(Paths.resolve(mode))` 原地换字段——**对象身份不变**。消费方统一
`from core.config import PATHS`，读 `PATHS.SESSION_DIR` 这类字段；`WORKDIR`、
`GLOBAL_SKILLS_DIR`、`PROJECT_SKILL_DIR` 不随 mode 变化，仍是普通模块常量。

理由：

- 旧写法（`_recompute_paths` + 一大串 `global` 重绑定）把同一份名单维护在三处
  （`global` 列表 / 赋值 / 测试的 `_PATH_NAMES`）。函数里漏一个 `global` 不会报错，只是
  静默创建局部变量、模块属性停在 dev 值。
- 消费方旧写法 `from core.config import SESSION_DIR` 在导入期快照值，正确性依赖
  「`configure_mode()` 先于一切 core 导入」。任何新入口（脚本、`python -m`、子进程）早导入
  一个 core 模块就会静默把 `.auth.json` / 权限规则写进 `./.nano-harness`。同一路径还会在
  不同模块里出现不同值（测试只能靠改消费方模块属性来隔离）。ADR-0004 已因同一
  「引用替换」问题否决过模块级全局 + 快照函数的组合。
- 稳定对象 + 字段晚绑定后，导入顺序纪律可以整段删除；`core/cli.py` 的延迟导入只剩
  「`--version` / 参数错误不付运行时启动成本」这一条理由。已知边界：日志 `FileHandler`
  在首次 `get_logger()` 时绑定当时的 `LOG_DIR`，`configure_mode()` 不会重定向——入口仍在
  任何 core 导入前 configure，所以实际运行不受影响。

测试隔离：字段可被 `monkeypatch.setattr(config.PATHS, "LOG_DIR", tmp_path)` 改写，
退出自动还原；同一用例里不要再调 `configure_mode()`（它会整表覆盖）。测试按
`dataclasses.fields(Paths)` 遍历字段，不必再维护第二份名字清单。

## Considered Options

- **稳定身份的 `PATHS` 快照 + 原地换字段**（采纳）：字段表即唯一名单，导入顺序不参与正确性；
  仍保留进程内切换 mode 的能力（测试与入口都需要）。
- **每次调用现取（`config.paths().SESSION_DIR`）**（拒绝）：调用点都带括号，也没有类型化的
  字段表；相比快照没有额外收益。
- **frozen dataclass + 代理对象（`__getattr__` 转发）**（拒绝）：为原子切换引入转发与类型检查
  特例，而切换只发生在启动期单线程，收益是假想竞态。
- **把 mode 变成环境变量、导入期只算一次**（拒绝）：仍要求入口在导入前设置；早导入只是从
  「静默写错目录」变成「静默用 dev 路径」，问题没消失，还失去测试内切换能力。
- **保持模块级常量**（拒绝）：见上，三处记账 + 导入顺序依赖 + 两种访问习惯并存。
