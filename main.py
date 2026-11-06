"""仓库内入口薄壳：`uv run main.py` 保持 dev 默认；真正的 CLI 在 core/cli.py。

顶层不 import 任何 core 模块：`import main` 不得让 core.* 进入 sys.modules，
延迟导入让薄壳保持零启动代价（tests/test_mode.py 锁定这条）。
"""


def main() -> None:
    from core.cli import main as cli_main
    cli_main(default_mode="dev")


if __name__ == "__main__":
    main()
