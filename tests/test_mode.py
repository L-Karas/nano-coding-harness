"""运行模式：--mode 解析、.nano-harness 根目录切换与导入顺序纪律。

mode 是进程级、导入期常量（core.config 的路径全部在导入期派生），
所以这里既测 configure_mode() 的重算结果，也用子进程锁住「未 configure 时默认 dev」。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import core.config as config
import main as entry
from core.bootstrap import init_harness

REPO_ROOT = Path(__file__).resolve().parents[1]

_PATH_NAMES = (
    "HARNESS_CONFIG_DIR", "LOG_DIR", "SKILL_DIR", "EXTENSION_DIR", "MEMORY_DIR",
    "TOOL_RESULTS_DIR", "MCP_DIR", "SESSION_ROOT_DIR", "SESSION_DIR", "HARNESS_SETTING_FILE",
    "SKILL_STATE_FILE", "PROVIDER_AUTH_FILE", "CUSTOM_PROVIDER_FILE", "CUSTOM_MODEL_FILE",
    "SESSION_INDEX_FILE", "MCP_CONFIG_FILE", "CRON_TASK_FILE", "PERMISSION_FILE",
)


@pytest.fixture
def restore_dev_mode():
    """任何切到 prod 的用例结束后都回到 dev，避免污染后续用例的路径常量。"""
    yield
    config.configure_mode("dev")


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path / config.HARNESS_DIR_NAME


def test_parse_args_defaults_to_dev():
    assert entry.parse_args([]).mode == "dev"


def test_parse_args_reads_mode():
    assert entry.parse_args(["--mode", "prod"]).mode == "prod"


def test_parse_args_rejects_unknown_arguments():
    """未知参数必须报错：静默退回 dev 会把配置、key 与会话写进项目目录。"""
    for argv in (["--smoke"], ["--mdoe", "prod"], ["prod"]):
        with pytest.raises(SystemExit) as exc:
            entry.parse_args(argv)
        assert exc.value.code == 2


@pytest.mark.parametrize("bad", ["Dev", "prod-mode"])
def test_parse_args_rejects_unknown_mode(bad):
    with pytest.raises(SystemExit) as exc:
        entry.parse_args(["--mode", bad])
    assert exc.value.code == 2


def test_dev_mode_uses_project_dir(restore_dev_mode):
    assert config.MODE == "dev"
    root = config.WORKDIR / config.HARNESS_DIR_NAME
    assert config.HARNESS_CONFIG_DIR == root
    assert config.SESSION_DIR == root / ".session" / config.format_workdir_name(config.WORKDIR)


def test_configure_mode_same_mode_is_noop(monkeypatch):
    """重复设置同一模式不重载 AgentConfig（不丢内存中未落盘的修改）。"""
    sentinel = config.AgentConfig(default_max_tokens=1234)
    monkeypatch.setattr(config, "CONFIGMANAGER", config.AgentConfigManager(sentinel))
    config.configure_mode("dev")
    assert config.CONFIGMANAGER.config is sentinel


def test_prod_mode_moves_every_path_to_home(fake_home, restore_dev_mode):
    config.configure_mode("prod")
    runtime_root = fake_home
    assert config.MODE == "prod"
    assert config.HARNESS_CONFIG_DIR == runtime_root
    for name in _PATH_NAMES:
        value = getattr(config, name)
        assert value == runtime_root or runtime_root in value.parents, f"{name} 未跟随 mode: {value}"
    # 会话目录仍按工作目录分目录，不会在 home 下把不同项目串在一起
    assert config.SESSION_DIR == runtime_root / ".session" / config.format_workdir_name(config.WORKDIR)
    # 项目级技能目录不受 mode 影响
    assert config.PROJECT_SKILL_DIR == config.WORKDIR / ".agents" / "skills"


def test_configure_mode_reloads_settings_from_new_root(fake_home, restore_dev_mode):
    root = fake_home
    root.mkdir(parents=True)
    (root / ".settings.json").write_text(json.dumps({"default_max_tokens": 4321}), encoding="utf-8")
    config.configure_mode("prod")
    assert config.CONFIGMANAGER.config.default_max_tokens == 4321


def test_init_harness_creates_tree_under_configured_root(fake_home, restore_dev_mode):
    """bootstrap 必须在调用时现取路径，而不是在导入期固化 dev 根。"""
    config.configure_mode("prod")
    init_harness()
    root = fake_home
    assert (root / ".settings.json").is_file()
    assert (root / ".mcp" / ".mcp.json").is_file()
    assert (root / ".session" / config.format_workdir_name(config.WORKDIR)).is_dir()
    assert (root / "log").is_dir()


def test_unconfigured_import_defaults_to_dev():
    """入口必须在导入其他 core 模块前 configure；裸导入 core.config 永远是 dev。"""
    code = "import core.config as c; print(c.MODE); print(c.HARNESS_CONFIG_DIR)"
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True)
    mode, root = result.stdout.splitlines()[:2]
    assert mode == "dev"
    assert Path(root) == REPO_ROOT / ".nano-harness"


def test_importing_entry_point_loads_no_core_module():
    """main.py 顶层只能 import 标准库：core.* 必须在 configure_mode 之后导入。"""
    code = ("import sys, main; "
            "print(sorted(m for m in sys.modules if m == 'core' or m.startswith('core.')))")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
