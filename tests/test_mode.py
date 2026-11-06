"""运行模式：-dev/-prod 解析、.nano-harness 根目录切换与 PATHS 快照身份。

mode 只由 configure_mode() 改写 PATHS 快照的字段（对象身份不变），
所以这里既测重算结果，也用子进程锁住「未 configure 时默认 dev」。
"""
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

import core.cli as cli_entry
import core.config as config
from core.bootstrap import init_harness

REPO_ROOT = Path(__file__).resolve().parents[1]

# 从 dataclass 字段派生：新增路径只改 core.config.Paths，这里不必同步
_PATH_NAMES = tuple(f.name for f in fields(config.Paths) if f.name != "MODE")


@pytest.fixture
def restore_dev_mode():
    """任何切到 prod 的用例结束后都回到 dev，避免污染后续用例的路径快照。"""
    yield
    config.configure_mode("dev")


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path / config.HARNESS_DIR_NAME


def run_parse_args(argv, *, default_mode="prod"):
    return cli_entry.parse_args(argv, default_mode=default_mode)


@pytest.mark.parametrize(("default_mode", "expected"), (("prod", "prod"), ("dev", "dev")))
def test_parse_args_follows_entry_default(default_mode, expected):
    """默认值随入口：nh/nano-harness = prod，main.py 薄壳 = dev（uv run main.py 行为不变）。"""
    assert run_parse_args([], default_mode=default_mode).mode == expected


@pytest.mark.parametrize(("flag", "default_mode", "expected"), (
    ("-dev", "prod", "dev"),
    ("--dev", "prod", "dev"),
    ("-prod", "dev", "prod"),
    ("--production", "dev", "prod"),
))
def test_parse_args_reads_mode_flags(flag, default_mode, expected):
    assert run_parse_args([flag], default_mode=default_mode).mode == expected


def test_parse_args_rejects_dev_and_prod_together():
    """互斥组：同时给两个模式开关直接报错，不让后出现的静默覆盖。"""
    with pytest.raises(SystemExit) as exc:
        run_parse_args(["-dev", "-prod"])
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", (
    ["--smoke"], ["--mdoe", "prod"], ["prod"],
    ["--mode", "prod"], ["--prod"], ["-p"], ["-pro"], ["-d"],
))
def test_parse_args_rejects_unknown_arguments(argv):
    """未知参数必须报错：静默退回默认模式会把配置、key 与会话写进错误目录。

    allow_abbrev=False：--prod / -p / -pro 不会被当作本参数的前缀缩写。
    """
    with pytest.raises(SystemExit) as exc:
        run_parse_args(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", (
    ["--production=1"], ["-prod=1"], ["-dev=1"], ["--dev=1"], ["-prod", "true"],
))
def test_parse_args_rejects_values_on_mode_flags(argv):
    """模式开关不接受取值；显式赋值与多余位置参数一律报错。"""
    with pytest.raises(SystemExit) as exc:
        run_parse_args(argv)
    assert exc.value.code == 2


def test_dev_mode_uses_project_dir(restore_dev_mode):
    assert config.PATHS.MODE == "dev"
    root = config.WORKDIR / config.HARNESS_DIR_NAME
    assert config.PATHS.HARNESS_CONFIG_DIR == root
    assert config.PATHS.SESSION_DIR == root / ".session" / config.format_workdir_name(config.WORKDIR)


def test_configure_mode_same_mode_is_noop(monkeypatch):
    """重复设置同一模式不重载 AgentConfig（不丢内存中未落盘的修改）。"""
    sentinel = config.AgentConfig(default_max_tokens=1234)
    monkeypatch.setattr(config, "CONFIGMANAGER", config.AgentConfigManager(sentinel))
    config.configure_mode("dev")
    assert config.CONFIGMANAGER.config is sentinel


def test_prod_mode_moves_every_path_to_home(fake_home, restore_dev_mode):
    config.configure_mode("prod")
    runtime_root = fake_home
    assert config.PATHS.MODE == "prod"
    assert config.PATHS.HARNESS_CONFIG_DIR == runtime_root
    for name in _PATH_NAMES:
        value = getattr(config.PATHS, name)
        assert value == runtime_root or runtime_root in value.parents, f"{name} 未跟随 mode: {value}"
    # 会话目录仍按工作目录分目录，不会在 home 下把不同项目串在一起
    assert config.PATHS.SESSION_DIR == runtime_root / ".session" / config.format_workdir_name(config.WORKDIR)
    # 项目级技能目录不受 mode 影响
    assert config.PROJECT_SKILL_DIR == config.WORKDIR / ".agents" / "skills"


def test_early_imported_paths_follow_later_mode_switch(fake_home, restore_dev_mode):
    """PATHS 对象身份稳定：早于 configure_mode 导入它的模块读到的仍是新 mode 的值。"""
    early = config.PATHS  # 等价于 `from core.config import PATHS` 的导入期绑定
    assert early.MODE == "dev"
    config.configure_mode("prod")
    assert early is config.PATHS
    assert (early.MODE, early.LOG_DIR) == ("prod", fake_home / "log")


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
    code = "import core.config as c; print(c.PATHS.MODE); print(c.PATHS.HARNESS_CONFIG_DIR)"
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True)
    mode, root = result.stdout.splitlines()[:2]
    assert mode == "dev"
    assert Path(root) == REPO_ROOT / ".nano-harness"


def test_skill_user_source_follows_mode(fake_home, restore_dev_mode):
    """技能 user 来源在扫描时现取，不能在导入期固化 dev 的 SKILL_DIR。"""
    from core.skill.skills import _skill_sources

    config.configure_mode("prod")
    assert dict(_skill_sources())["user"] == fake_home / "skills"


def test_importing_entry_point_loads_no_core_module():
    """main.py 保持薄壳：顶层不加载 core.*（解析参数前的导入都会拖慢启动并引入副作用）。"""
    code = ("import sys, main; "
            "print(sorted(m for m in sys.modules if m == 'core' or m.startswith('core.')))")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


def test_importing_cli_module_loads_no_runtime_modules():
    """core.cli 顶层只 import 标准库：`nh --version` / 参数错误不付完整运行时的启动成本。"""
    code = ("import sys, core.cli; "
            "print(sorted(m for m in sys.modules "
            "if m == 'core.config' or m.startswith('core.config.') or m == 'core.agent_loop'))")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
