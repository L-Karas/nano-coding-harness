import json
from pathlib import Path
from typing import Literal

import dotenv
from pydantic import BaseModel, Field, model_validator

dotenv.load_dotenv()

WORKDIR = Path.cwd()
HARNESS_CONFIG_DIR = WORKDIR / ".harness"
LOG_DIR = HARNESS_CONFIG_DIR / "log"
SKILL_DIR = HARNESS_CONFIG_DIR / "skills"
MEMORY_DIR = HARNESS_CONFIG_DIR / ".memory"
SESSION_DIR = HARNESS_CONFIG_DIR / ".session"
TOOL_RESULTS_DIR = HARNESS_CONFIG_DIR / ".task_outputs" / "tool_results"
TASK_DIR = HARNESS_CONFIG_DIR / ".tasks"

HARNESS_SETTING_FILE = HARNESS_CONFIG_DIR / ".settings.json"
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

_CONFIG_DIRS = (HARNESS_CONFIG_DIR, LOG_DIR, SKILL_DIR, MEMORY_DIR, SESSION_DIR, TOOL_RESULTS_DIR, TASK_DIR)
_JSON_FILES = {
    HARNESS_SETTING_FILE: {},  # 默认值由 AgentConfig 提供，load_config 读取时补齐
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


def _resolve_tokens(name: str, value: float | int, base: int) -> int:
    """token 预算字段统一解析：值 <= 1 视为 base 的比例，否则视为绝对值；超界抛 ValueError。"""
    if value <= 1:
        return int(value * base)
    if value > base:
        raise ValueError(f"{name} must be <= {base}")
    return int(value)


class AgentConfig(BaseModel):
    default_model: str = Field(default="", description="Default model id, 'provider:model'")
    default_thinking_level: Literal["minimal", "low", "medium", "high", "max"] = (
        Field(default="max", description="Default reasoning effort for thinking mode"))
    default_sub_model: str = Field(default="",
                                   description="Default model for sub-agents; falls back to default_model. Supports 'provider:model' to use a model from another provider")
    default_sub_model_thinking_level: Literal["minimal", "low", "medium", "high", "max"] = (
        Field(default="max",
              description="Reasoning effort for default_sub_model; falls back to default_thinking_level when default_sub_model is unset"))
    default_fallback_model: str = Field(default="",
                                        description="Model used when the default model is unavailable. Supports 'provider:model' to use a model from another provider")

    model_context_length: int = Field(default=1_000_000, description="Model context window size in tokens")
    model_max_output: float | int = Field(default=0.2,
                                          description="Max output tokens the model supports. Values <= 1 are a fraction of model_context_length; larger values are absolute token counts. Resolved to an absolute int by the validator",
                                          gt=0)
    default_max_tokens: int = Field(default=12_000, description="Max tokens per default model call")
    escalated_max_tokens: int = Field(default=24_000,
                                      description="Max tokens for escalated calls when the default limit is insufficient")

    max_retries: int = Field(default=3, description="Max retries for a failed model call")
    max_recovery_retries: int = Field(default=3,
                                      description="Max call attempts to continue generation after the model hits its output token limit")
    base_retry_delay_ms: int = Field(default=500,
                                     description="Base delay in milliseconds before retrying an overloaded server")

    auto_compact: bool = Field(default=True, description="Enable automatic context compaction")
    compact_type: str = Field(default="summary", description="Context compaction method")
    compact_threshold: float | int = Field(default=0.5,
                                           description="Context usage that triggers compaction. Values <= 1 are a fraction of model_context_length; larger values are absolute token counts",
                                           gt=0)
    reserve_threshold: float | int = Field(default=0.2,
                                           description="Recent message tokens kept after compaction. Values <= 1 are a fraction of compact_threshold; larger values are absolute token counts",
                                           ge=0)
    persist_tool_tokens: int = Field(default=3_000,
                                     description="Inline tool-result budget: oversized fresh outputs are persisted to a file (compared in characters, preview keeps this many characters); older results above this estimated token count are cleared by micro-compaction")
    summary_max_tokens: int = Field(default=20_000, description="Max tokens for the summary produced during compaction")
    keep_recent_tool_results: int = Field(default=30,
                                          description="Most recent tool results kept before micro-compaction clears older ones")

    enable_experimental_tools: bool = Field(default=False, description="Enable experimental features and tools")
    enable_defer_tools: bool = Field(default=False, description="Enable deferred tool loading")
    enable_defer_skills: bool = Field(default=False, description="Enable deferred skill loading")

    @model_validator(mode="after")
    def resolve_token_limits(self):
        """把各 token 字段解析为绝对值（比例 / 绝对二选一），并校验字段间大小约束。"""
        self.model_max_output = _resolve_tokens(
            "model_max_output", self.model_max_output, self.model_context_length)
        # 单次调用上限必须落在模型最大输出之内。
        if self.default_max_tokens > self.model_max_output:
            raise ValueError(
                "default_max_tokens must be <= model_max_output (max output tokens supported by the model)")
        if self.escalated_max_tokens > self.model_max_output:
            raise ValueError(
                "escalated_max_tokens must be <= model_max_output (max output tokens supported by the model)")

        self.compact_threshold = _resolve_tokens(
            "compact_threshold", self.compact_threshold, self.model_context_length)
        # reserve 以解析后的 compact_threshold 为基数，故在其后解析。
        self.reserve_threshold = _resolve_tokens(
            "reserve_threshold", self.reserve_threshold, int(self.compact_threshold))
        return self


class AgentConfigManager:

    def __init__(self, config: AgentConfig):
        self.config = config

    def save_config(self):
        HARNESS_SETTING_FILE.write_text(self.config.model_dump_json(ensure_ascii=False, indent=4), encoding="utf-8")
        CONFIGMANAGER.config = self.config  # 同步运行中的单例；消费方持有 manager 引用，需改属性而非重新绑定

    @classmethod
    def load_config(cls) -> "AgentConfigManager":
        if not HARNESS_SETTING_FILE.exists():
            return cls(config=AgentConfig())

        config = AgentConfig.model_validate_json(HARNESS_SETTING_FILE.read_text(encoding="utf-8"))
        return cls(config=config)


CONFIGMANAGER = AgentConfigManager.load_config()
