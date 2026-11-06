# 全局命令 nh / nano-harness 与双入口默认模式

## Status

accepted

`pyproject.toml` 声明 `[project.scripts]`：`nh` 与 `nano-harness` 都指向 `core.cli:main`，
用 `uv tool install --editable .` 装进独立 tool venv，shim 落到 `~/.local/bin`
（Windows 为 `%USERPROFILE%\.local\bin`，该目录需在 PATH 中）。
CLI 从根目录 `main.py` 搬进 `core/cli.py`：顶层只 import 标准库，`main(default_mode=...)`
先解析 `-dev/--dev` 与 `-prod/--production`（互斥），再 `configure_mode()`，最后才导入
`core.agent_loop` 等（当时）导入期绑定路径常量的模块。仓库根 `main.py` 保留为薄壳，函数体内延迟导入
`core.cli` 并传 `default_mode="dev"`，因此 `uv run main.py` 行为不变。

两个入口的缺省模式不同：全局命令缺省 `prod`（`~/.nano-harness`），仓库入口缺省 `dev`
（`<cwd>/.nano-harness`）。理由：全局命令在任意目录运行，缺省 dev 会把 `.nano-harness`
撒进每个项目、且新目录没有 auth / 模型配置；仓库入口的 dev 默认是既有开发仪式，README 与测试
都依赖它。`-dev` / `-prod` 在两个入口都能显式覆盖。

wheel 只装 `core/`（含 `tui/app.css`、`client/models.json` 等资源），docs / examples / tests /
temp / smoke 不进 site-packages；`--version` 在源码树内读 `pyproject.toml`（editable 安装下
跟随当前代码），普通 wheel 安装读发行版元数据，最后兜底 `0.1.0`。

已知限制：editable 安装下改依赖或版本号后需要 `uv tool install --editable . --force` 重装；
prod 的权限 / 扩展 / MCP 配置跨项目全局共享，这是既有 prod 语义。

> 注：路径绑定方式后由 [ADR-0014](0014-paths-snapshot.md) 改为 `core.config.PATHS` 快照，
> 「先 `configure_mode()` 再导入其他 core 模块」的导入顺序要求不再必要；两个入口与缺省模式的
> 决策不变。

## Considered Options

- **标准 console scripts + uv tool editable 安装**（采纳）：与“可安装工具”语义一致，卸载 / 升级
  有现成命令；editable 让仓库改代码立即生效。代价是本仓库必须补上打包元数据（此前 `uv build`
  因 flat-layout 多顶层包直接失败）。
- **只装进仓库 `.venv` 的 Scripts**（拒绝）：用户 PATH 里没有该目录，不天然全局可用；venv 删除
  后命令消失。
- **PATH 里放 `.cmd` / shell 包装脚本**（拒绝）：绑死仓库绝对路径，每次启动多一层 uv 依赖解析，
  且没有 wheel，无法安装到其它机器。
- **统一两个入口的默认模式为 prod**（拒绝）：会让 `uv run main.py` 把配置写进 home，破坏现有
  README 与测试的 dev 默认；反过来统一为 dev 则会把配置撒进任意目录。
- **自动探测 `<cwd>/.nano-harness` 决定模式**（拒绝）：隐式切换难以排查，残留目录会改变行为。
