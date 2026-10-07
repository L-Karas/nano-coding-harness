"""core/bootstrap.py 的 .harness 初始化测试。"""
import json
import threading
import time

import core.bootstrap as bootstrap_mod
import core.client.model as model_mod
import core.cron_scheduler as cron_mod
import core.hook.hook as hook_mod
import core.skill.skills as skills_mod
from core.bootstrap import init_harness


def test_init_harness_creates_files(tmp_path, monkeypatch):
    dirs, files = {}, {}
    for name in ("HARNESS_CONFIG_DIR", "LOG_DIR", "SKILL_DIR", "MEMORY_DIR",
                 "SESSION_DIR", "TOOL_RESULTS_DIR", "TASK_DIR", "MCP_DIR",
                 "MAILBOX_DIR", "WORKTREES_DIR"):
        dirs[name] = tmp_path / name
    for name in ("HARNESS_SETTING_FILE", "PROVIDER_AUTH_FILE", "CUSTOM_PROVIDER_FILE",
                 "CUSTOM_MODEL_FILE", "MCP_CONFIG_FILE", "SESSION_INDEX_FILE", "CRON_TASK_FILE"):
        files[name] = tmp_path / f"{name}.tmp"
    monkeypatch.setattr(bootstrap_mod, "_CONFIG_DIRS", tuple(dirs.values()))
    monkeypatch.setattr(bootstrap_mod, "_JSON_FILES",
                        {files["HARNESS_SETTING_FILE"]: {"default_model": "p:m"},
                         files["PROVIDER_AUTH_FILE"]: {}})
    monkeypatch.setattr(bootstrap_mod, "_TOUCH_FILES",
                        (files["SESSION_INDEX_FILE"], files["CRON_TASK_FILE"]))

    init_harness()
    init_harness()  # 幂等

    assert all(p.is_dir() for p in dirs.values())
    assert json.loads(files["HARNESS_SETTING_FILE"].read_text(encoding="utf-8")) == {
        "default_model": "p:m"}
    assert files["PROVIDER_AUTH_FILE"].exists()
    assert files["SESSION_INDEX_FILE"].exists() and files["CRON_TASK_FILE"].exists()


def test_bootstrap_initializes_once_across_threads(monkeypatch):
    """并发首次调用 bootstrap 只初始化一次（避免重复启动 cron 线程）。"""
    calls, gate = [], threading.Barrier(8)

    def _record(name):
        def _step():
            time.sleep(0.01)  # 拉长竞争窗口：若无锁，多个线程会各跑一遍初始化
            calls.append(name)
        return _step

    monkeypatch.setattr(bootstrap_mod, "_booted", False)
    monkeypatch.setattr(bootstrap_mod, "init_harness", _record("harness"))
    monkeypatch.setattr(model_mod, "load_model_registry", _record("registry"))
    monkeypatch.setattr(cron_mod, "start_cron_scheduler", _record("cron"))
    monkeypatch.setattr(hook_mod, "install_default_hooks", _record("hooks"))
    monkeypatch.setattr(skills_mod, "scan_skills", _record("skills"))

    def _call():
        gate.wait(timeout=5)
        bootstrap_mod.bootstrap()

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sorted(calls) == ["cron", "harness", "hooks", "registry", "skills"], calls
