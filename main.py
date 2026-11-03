"""入口：先解析 -prod/--production 选定 .nano-harness 根目录，再把真实 agent 回合接进 Textual UI。"""
import argparse


def parse_args() -> argparse.Namespace:
    """只接受 -prod/--production；拼错/多余参数直接报错（静默退回 dev 会把配置写进项目目录）。"""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="nano-harness TUI；-prod/--production 决定 .nano-harness 的位置。")
    parser.add_argument(
        "-prod", "--production", action="store_true",
        help="生产模式：.nano-harness 位于 ~/.nano-harness；缺省为开发模式（<项目>/.nano-harness）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 必须先定 mode 再导入其他 core 模块：它们在导入期绑定 core.config 的路径常量
    from core.config import configure_mode
    configure_mode("prod" if args.production else "dev")

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
