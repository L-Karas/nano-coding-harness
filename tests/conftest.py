"""测试进程初始化：与生产 core.bootstrap 同源，但不启动 cron 后台线程。

各测试模块在导入期就会调用 get_logger() 写日志、读模型注册表；这里先建 .harness，
再加载内置扩展 / 技能 / 模型注册表，使测试环境与运行进程的默认状态一致。
空规则注册内置扩展：测试不依赖工作目录下的 .permissions.json。
"""
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from core.bootstrap import init_harness

init_harness()

from core.client.model import load_model_registry  # noqa: E402
from core.config import AgentConfig  # noqa: E402
import core.context.session.session as session_module  # noqa: E402
from core.context.session.session import SessionManager  # noqa: E402
from core.extension import reset_extensions  # noqa: E402
from core.extension.builtin import permission, register_builtin_extensions  # noqa: E402
from core.extension.builtin.permission import Rules  # noqa: E402
import core.skill.skills as skills_mod  # noqa: E402

register_builtin_extensions(rules=Rules())
# 测试不读写开发者真实的 .harness/.skills.json；启动扫描后固定为全员启用
skills_mod.SKILL_STATE_FILE = Path(
    tempfile.mkdtemp(prefix="nano-harness-skills-")) / ".skills.json"
skills_mod.scan_skills()
skills_mod._DISABLED = set()
skills_mod._STATE_ERROR = None
load_model_registry()


@pytest.fixture
def session_manager(tmp_path, monkeypatch):
    """SessionManager 及落盘目录全部隔离到 tmp_path（会话测试共用）。"""
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


@pytest.fixture
def empty_extension_registry():
    """需要自行登记扩展的测试：每个用例从空 registry 开始，结束后清空。"""
    reset_extensions()
    yield
    reset_extensions()


@pytest.fixture
def full_extension_registry():
    """扩展接线测试的干净起点：内置扩展注册后运行，结束时清空。"""
    reset_extensions()
    register_builtin_extensions(rules=Rules())
    yield
    reset_extensions()


@pytest.fixture(autouse=True)
def _manual_approval_mode(monkeypatch):
    """权限门默认层固定为 Manual：测试不依赖本机 .settings.json 的 approval_mode。"""
    monkeypatch.setattr(permission, "CONFIGMANAGER",
                        SimpleNamespace(config=AgentConfig(approval_mode="Manual")))


@pytest.fixture
def noise_png():
    """噪点 PNG 工厂：噪点压不下去，字节数随像素近似线性增长，用来造真实可解码的「超限」样本。"""

    def make(width: int = 400, height: int = 400) -> bytes:
        buf = io.BytesIO()
        Image.frombytes("RGB", (width, height), os.urandom(width * height * 3)).save(
            buf, format="PNG")
        return buf.getvalue()

    return make
