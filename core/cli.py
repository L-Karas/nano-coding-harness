"""命令行入口：解析模式开关选定 .nano-harness 根，再把真实 agent 回合接进 Textual UI。

本模块顶层只 import 标准库：`core.config` / `core.agent_loop` 等必须在 `configure_mode()` 之后
导入，否则它们会在导入期绑定 dev 路径（导入顺序由 tests/test_mode.py 锁定）。

两个控制台脚本 `nh` / `nano-harness` 都指向 `main()`（缺省 prod）；仓库根 `main.py` 是
传 `default_mode="dev"` 的薄壳，保持 `uv run main.py` 的既有行为。
"""
import argparse
import importlib.metadata
import sys
import tomllib
from pathlib import Path

_PACKAGE_NAME = "nano-harness"
_FALLBACK_VERSION = "0.1.0"
# core/cli.py -> 仓库根；装成普通 wheel 后该目录是 site-packages，没有 pyproject.toml
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _source_version() -> str | None:
    """源码树里的 pyproject 版本；editable 安装下 `--version` 因此跟随当前代码。"""
    try:
        with (_SOURCE_ROOT / "pyproject.toml").open("rb") as handle:
            return tomllib.load(handle)["project"]["version"]
    except (OSError, KeyError, ValueError):  # 含 TOMLDecodeError / UnicodeDecodeError
        return None


def _dist_version() -> str | None:
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def version() -> str:
    """版本来源优先级：源码树 pyproject → 发行版元数据 → 常量兜底。"""
    return _source_version() or _dist_version() or _FALLBACK_VERSION


def parse_args(argv: list[str] | None = None, *, default_mode: str = "prod") -> argparse.Namespace:
    """解析参数；未知参数 / 同时给 -dev 与 -prod 一律报错（静默退回默认会把配置写错目录）。"""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        prog=Path(sys.argv[0]).stem or "nano-harness",
        description=f"nano-harness TUI（本入口缺省 {default_mode} 模式）")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "-dev", "--dev", dest="mode", action="store_const", const="dev",
        help="开发模式：.nano-harness 位于当前工作目录（配置随项目走）")
    mode_group.add_argument(
        "-prod", "--production", dest="mode", action="store_const", const="prod",
        help="生产模式：.nano-harness 位于 ~/.nano-harness（配置跨项目共享）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version()}")
    parser.set_defaults(mode=default_mode)
    return parser.parse_args(argv)


def main(default_mode: str = "prod") -> None:
    """入口：先定 mode 再导入其他 core 模块（它们在导入期绑定 core.config 的路径常量）。"""
    args = parse_args(default_mode=default_mode)

    from core.config import configure_mode
    configure_mode(args.mode)

    from core.agent_loop import make_agent_turn, start_agent_runtime
    from core.context.session import SESSION_MANAGER
    from core.tui.ui_textual import run

    runtime = start_agent_runtime()
    run(
        handle_query=make_agent_turn(runtime),
        session_manager=SESSION_MANAGER,
        on_interrupt=runtime.interrupt,
        runtime=runtime,
    )
