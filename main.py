"""入口：把 core.loop_with_interrupt 的真实 agent 回合接进 Textual UI。"""
from core.context.session import SESSION_MANAGER
from core.loop_with_interrupt import make_agent_turn, start_agent_runtime
from core.tui.ui_textual import run


def main():
    runtime = start_agent_runtime()
    run(handle_query=make_agent_turn(runtime), session_manager=SESSION_MANAGER,
        on_interrupt=runtime.interrupt, runtime=runtime)


if __name__ == "__main__":
    main()
