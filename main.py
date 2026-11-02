"""入口：先解析 --mode 选定 .nano-harness 根目录，再把真实 agent 回合接进 Textual UI。"""
import argparse
import sys


def parse_args(argv: list[str]) -> argparse.Namespace:
    """只接受 --mode；拼错/多余参数直接报错（静默退回 dev 会把配置写进项目目录）。"""
    parser = argparse.ArgumentParser(
        description="nano-harness TUI；--mode 决定 .nano-harness 的位置。")
    parser.add_argument(
        "--mode", choices=("dev", "prod"), default="dev",
        help="dev: <项目>/.nano-harness（默认）；prod: ~/.nano-harness")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    # 必须先定 mode 再导入其他 core 模块：它们在导入期绑定 core.config 的路径常量
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


if __name__ == "__main__":
    main()
