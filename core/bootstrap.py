"""进程启动初始化（composition root）。

各模块导入自身不再创建目录 / 启动线程 / 扫描磁盘；由这里显式按序初始化：
.nano-harness 目录树与文件 → 内置扩展 → 技能扫描 → 模型注册表 → cron 调度线程。
`bootstrap()` 幂等，供 main.py / TUI run() / start_agent_runtime() 调用；
测试用 tests/conftest.py 复用其中的单项初始化（不启动后台线程）。
"""
import json
import threading
from pathlib import Path

import core.config as config


def _config_dirs() -> tuple[Path, ...]:
    """每次调用现取路径：入口 configure_mode() 可能已把 .nano-harness 根换到 home。"""
    return (config.HARNESS_CONFIG_DIR, config.LOG_DIR, config.SKILL_DIR, config.EXTENSION_DIR,
            config.MEMORY_DIR, config.SESSION_DIR, config.TOOL_RESULTS_DIR, config.MCP_DIR)


def _json_files() -> dict[Path, dict]:
    return {
        config.HARNESS_SETTING_FILE: {},  # 默认值由 AgentConfig 提供，load_config 读取时补齐
        config.SKILL_STATE_FILE: {"disabled": []},
        config.PROVIDER_AUTH_FILE: {},
        config.CUSTOM_PROVIDER_FILE: {},
        config.CUSTOM_MODEL_FILE: {},
        config.MCP_CONFIG_FILE: {"mcpServers": {}},
        config.PERMISSION_FILE: {},
    }


def _touch_files() -> tuple[Path, ...]:
    return (config.SESSION_INDEX_FILE, config.CRON_TASK_FILE)


_booted = False
_boot_lock = threading.Lock()


def init_harness() -> None:
    """创建 .nano-harness 目录树与初始 JSON / 占位文件；幂等。"""
    for directory in _config_dirs():
        directory.mkdir(parents=True, exist_ok=True)
    for path, default in _json_files().items():
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=4), encoding="utf-8")
    for path in _touch_files():
        path.touch(exist_ok=True)


def bootstrap() -> None:
    """完整启动初始化；幂等且线程安全（并发首次调用只初始化一次）。"""
    global _booted
    with _boot_lock:
        if _booted:
            return

        init_harness()

        # 函数内导入：bootstrap 被导入时不拉起模型注册表 / 工具池等重依赖
        from core.client.model import load_model_registry
        from core.cron_scheduler import start_cron_scheduler
        from core.extension.builtin import register_builtin_extensions
        from core.extension.loader import load_extensions
        from core.skill.skills import scan_skills

        register_builtin_extensions()
        load_extensions()
        scan_skills()
        load_model_registry()
        start_cron_scheduler()

        _booted = True
