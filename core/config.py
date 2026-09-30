import json
from pathlib import Path

WORKDIR = Path.cwd()
HARNESS_CONFIG_DIR = WORKDIR / ".harness"
LOG_DIR = HARNESS_CONFIG_DIR / "log"
SKILL_DIR = HARNESS_CONFIG_DIR / "skills"
MEMORY_DIR = HARNESS_CONFIG_DIR / ".memory"
SESSION_DIR = HARNESS_CONFIG_DIR / ".session"
TRANSCRIPT_DIR = HARNESS_CONFIG_DIR / ".transcripts"
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

DEFAULT_MAX_TOKENS = int(1.6e4)
ESCALATED_MAX_TOKENS = int(3.2e4)
MAX_RETRIES = 3
MAX_RECOVERY_RETRIES = 2
MAX_CONSECUTIVE = 2

BASE_DELAY_MS = 500
CONTEXT_LIMIT = int(2e5)
RESERVE_TOKENS = int(4e4)
KEEP_RECENT_TOOL_RESULTS = 30
PERSIST_THRESHOLD = int(3e3)
SUMMARIZE_MAX_TOKENS = int(2e4)


def _init_harness():
    if not HARNESS_CONFIG_DIR.exists():
        HARNESS_CONFIG_DIR.mkdir(parents=True)
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(parents=True)
    if not SKILL_DIR.exists():
        SKILL_DIR.mkdir(parents=True)
    if not MEMORY_DIR.exists():
        MEMORY_DIR.mkdir(parents=True)
    if not SESSION_DIR.exists():
        SESSION_DIR.mkdir(parents=True)
    if not TRANSCRIPT_DIR.exists():
        TRANSCRIPT_DIR.mkdir(parents=True)
    if not TOOL_RESULTS_DIR.exists():
        TOOL_RESULTS_DIR.mkdir(parents=True)
    if not TASK_DIR.exists():
        TASK_DIR.mkdir(parents=True)

    if not HARNESS_SETTING_FILE.exists():
        with HARNESS_SETTING_FILE.open("x") as f:
            f.write(json.dumps({
                "default_provider": "",
                "default_model": "",
                "default_thinking_level": "max",
                "default_sub_model": "",
                "default_fallback_model": "",
            }, ensure_ascii=False, indent=4))
    if not PROVIDER_AUTH_FILE.exists():
        with PROVIDER_AUTH_FILE.open("x") as f:
            f.write(json.dumps({}, ensure_ascii=False, indent=4))
    if not CUSTOM_PROVIDER_FILE.exists():
        with CUSTOM_PROVIDER_FILE.open("x") as f:
            f.write(json.dumps({}, ensure_ascii=False, indent=4))
    if not CUSTOM_MODEL_FILE.exists():
        with CUSTOM_MODEL_FILE.open("x") as f:
            f.write(json.dumps({}, ensure_ascii=False, indent=4))
    if not SESSION_INDEX_FILE.exists():
        SESSION_INDEX_FILE.touch()
    if not MCP_CONFIG_FILE.exists():
        with MCP_CONFIG_FILE.open("x") as f:
            f.write(json.dumps({
                "mcpServers": {}
            }, ensure_ascii=False, indent=4))
    if not CRON_TASK_FILE.exists():
        CRON_TASK_FILE.touch()


_init_harness()
