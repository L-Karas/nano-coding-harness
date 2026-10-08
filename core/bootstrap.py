"""进程启动初始化（composition root）。

各模块导入自身不再创建目录 / 启动线程 / 扫描磁盘；由这里显式按序初始化：
.harness 目录树与文件 → 出厂 hook → 技能扫描 → 模型注册表 → cron 调度线程。
`bootstrap()` 幂等，供 main.py / TUI run() / start_agent_runtime() 调用；
测试用 tests/conftest.py 复用其中的单项初始化（不启动后台线程）。
"""
import json
import threading

from core.config import (
    CRON_TASK_FILE,
    CUSTOM_MODEL_FILE,
    CUSTOM_PROVIDER_FILE,
    EXTENSION_DIR,
    HARNESS_CONFIG_DIR,
    HARNESS_SETTING_FILE,
    LOG_DIR,
    MAILBOX_DIR,
    MEMORY_DIR,
    MCP_CONFIG_FILE,
    MCP_DIR,
    PROVIDER_AUTH_FILE,
    SESSION_DIR,
    SESSION_INDEX_FILE,
    SKILL_DIR,
    TASK_DIR,
    TOOL_RESULTS_DIR,
    WORKTREES_DIR,
)

_CONFIG_DIRS = (HARNESS_CONFIG_DIR, LOG_DIR, SKILL_DIR, EXTENSION_DIR, MEMORY_DIR,
                SESSION_DIR, TOOL_RESULTS_DIR, TASK_DIR, MCP_DIR, MAILBOX_DIR,
                WORKTREES_DIR)
_JSON_FILES = {
    HARNESS_SETTING_FILE: {},  # 默认值由 AgentConfig 提供，load_config 读取时补齐
    PROVIDER_AUTH_FILE: {},
    CUSTOM_PROVIDER_FILE: {},
    CUSTOM_MODEL_FILE: {},
    MCP_CONFIG_FILE: {"mcpServers": {}},
}
_TOUCH_FILES = (SESSION_INDEX_FILE, CRON_TASK_FILE)

_booted = False
_boot_lock = threading.Lock()


def init_harness() -> None:
    """创建 .harness 目录树与初始 JSON / 占位文件；幂等。"""
    for directory in _CONFIG_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
    for path, default in _JSON_FILES.items():
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=4), encoding="utf-8")
    for path in _TOUCH_FILES:
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
        from core.extension.loader import load_extensions
        from core.hook.hook import install_default_hooks
        from core.skill.skills import scan_skills

        install_default_hooks()
        load_extensions()
        scan_skills()
        load_model_registry()
        start_cron_scheduler()

        _booted = True
