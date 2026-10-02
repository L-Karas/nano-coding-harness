import json
from pathlib import Path

WORKDIR = Path.cwd()
HARNESS_CONFIG_DIR = WORKDIR / ".harness"
LOG_DIR = HARNESS_CONFIG_DIR / "log"
SKILL_DIR = HARNESS_CONFIG_DIR / "skills"
MEMORY_DIR = HARNESS_CONFIG_DIR / ".memory"
SESSION_DIR = HARNESS_CONFIG_DIR / ".session"
TOOL_RESULTS_DIR = HARNESS_CONFIG_DIR / ".task_outputs" / "tool_results"
TASK_DIR = HARNESS_CONFIG_DIR / ".tasks"

HARNESS_SETTING_FILE = HARNESS_CONFIG_DIR / ".setting.json"
PROVIDER_AUTH_FILE = HARNESS_CONFIG_DIR / ".auth.json"
CUSTOM_PROVIDER_FILE = HARNESS_CONFIG_DIR / ".custom_providers.json"
CUSTOM_MODEL_FILE = HARNESS_CONFIG_DIR / ".custom_models.json"
SESSION_INDEX_FILE = HARNESS_CONFIG_DIR / ".session" / "session_index.jsonl"
MCP_CONFIG_FILE = HARNESS_CONFIG_DIR / ".mcp" / ".mcp.json"
CRON_TASK_FILE = HARNESS_CONFIG_DIR / ".scheduled_tasks.json"

# 工具失败统一前缀（call_tool_handler 返回串由此生成；loop_with_interrupt 的 tool_failed 判定
# 与 TUI 渲染 error 卡共用）。放 config 叶子模块：core.tools 会拉起工具注册表，
# core.tui.render 顶层 import core.tools 会经 hook_permission 成环。
TOOL_ERROR_PREFIXES = ("[Tool Error]:", "[Unknown Tool]:")

MESSAGE_PREVIEW_CHARS = 30  # 用户消息截断展示长度（/fork 列表预览与 fork 会话标题 [Fork] 共用）

DEFAULT_MAX_TOKENS = int(1.6e4)
ESCALATED_MAX_TOKENS = int(3.2e4)
MAX_RETRIES = 3
MAX_RECOVERY_RETRIES = 2

BASE_DELAY_MS = 500
CONTEXT_LIMIT = int(2e5)
RESERVE_TOKENS = int(4e4)
KEEP_RECENT_TOOL_RESULTS = 30
PERSIST_THRESHOLD = int(3e3)
SUMMARIZE_MAX_TOKENS = int(2e4)


_CONFIG_DIRS = (HARNESS_CONFIG_DIR, LOG_DIR, SKILL_DIR, MEMORY_DIR, SESSION_DIR, TOOL_RESULTS_DIR, TASK_DIR)
_JSON_FILES = {
    HARNESS_SETTING_FILE: {
        "default_provider": "",
        "default_model": "",
        "default_thinking_level": "max",
        "default_sub_model": "",
        "default_fallback_model": "",
    },
    PROVIDER_AUTH_FILE: {},
    CUSTOM_PROVIDER_FILE: {},
    CUSTOM_MODEL_FILE: {},
    MCP_CONFIG_FILE: {"mcpServers": {}},
}
_TOUCH_FILES = (SESSION_INDEX_FILE, CRON_TASK_FILE)


def _init_harness():
    for directory in _CONFIG_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
    for path, default in _JSON_FILES.items():
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=4), encoding="utf-8")
    for path in _TOUCH_FILES:
        path.touch(exist_ok=True)


_init_harness()
