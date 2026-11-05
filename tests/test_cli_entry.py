"""控制台脚本与版本来源：pyproject 的 scripts 必须指向真实 CLI，wheel 只装 core/。"""
import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import core.cli as cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_console_scripts_point_to_core_cli():
    """nh 与 nano-harness 是同一入口的两个命令名，不允许漂移成两份实现。"""
    scripts = _pyproject()["project"]["scripts"]
    assert scripts == {"nh": "core.cli:main", "nano-harness": "core.cli:main"}


def test_wheel_ships_only_core_package():
    """wheel 只装运行时代码；docs / examples / tests / temp / smoke 不进 site-packages。"""
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["core"]


def test_version_prefers_source_pyproject(monkeypatch):
    """源码树（含 editable 安装）里 --version 跟随当前 pyproject，不读安装期的旧元数据。"""
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda _name: "9.9.9")
    assert cli.version() == _pyproject()["project"]["version"]


def test_version_falls_back_to_installed_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_SOURCE_ROOT", tmp_path)
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda _name: "9.9.9")
    assert cli.version() == "9.9.9"


def test_version_falls_back_to_constant_without_distribution(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_SOURCE_ROOT", tmp_path)

    def missing(_name):
        raise importlib.metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(cli.importlib.metadata, "version", missing)
    assert cli.version() == "0.1.0"


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--version"])
    assert exc.value.code == 0
    assert cli.version() in capsys.readouterr().out
