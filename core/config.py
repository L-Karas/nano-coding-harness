from pathlib import Path
from typing import Literal, Optional

import dotenv
from pydantic import BaseModel, Field

dotenv.load_dotenv()

WORKDIR = Path.cwd()
HARNESS_CONFIG_DIR = WORKDIR / ".harness"
LOG_DIR = HARNESS_CONFIG_DIR / "log"
SKILL_DIR = HARNESS_CONFIG_DIR / "skills"
MEMORY_DIR = HARNESS_CONFIG_DIR / ".memory"
SESSION_DIR = HARNESS_CONFIG_DIR / ".session"
TOOL_RESULTS_DIR = HARNESS_CONFIG_DIR / ".task_outputs" / "tool_results"
TASK_DIR = HARNESS_CONFIG_DIR / ".tasks"
MCP_DIR = HARNESS_CONFIG_DIR / ".mcp"
MAILBOX_DIR = HARNESS_CONFIG_DIR / ".mailboxes"
WORKTREES_DIR = HARNESS_CONFIG_DIR / ".worktrees"

HARNESS_SETTING_FILE = HARNESS_CONFIG_DIR / ".settings.json"
PROVIDER_AUTH_FILE = HARNESS_CONFIG_DIR / ".auth.json"
CUSTOM_PROVIDER_FILE = HARNESS_CONFIG_DIR / ".custom_providers.json"
CUSTOM_MODEL_FILE = HARNESS_CONFIG_DIR / ".custom_models.json"
SESSION_INDEX_FILE = HARNESS_CONFIG_DIR / ".session" / "session_index.jsonl"
MCP_CONFIG_FILE = MCP_DIR / ".mcp.json"
CRON_TASK_FILE = HARNESS_CONFIG_DIR / ".scheduled_tasks.json"


def _scale_tokens(value: float | int, base: int) -> int:
    """运行时 token 解析：值 <= 1 视为 base 的比例，否则视为绝对值；绝对值超界时封顶（不抛错）。"""
    return int(value * base) if value <= 1 else min(int(value), base)


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

    default_max_tokens: int = Field(default=16_000, description="Max tokens per default model call")
    escalated_max_tokens: int = Field(default=32_000,
                                      description="Max tokens for escalated calls when the default limit is insufficient; also the output headroom kept by compaction when the model declares no fixed max output")

    max_retries: int = Field(default=3, description="Max retries for a failed model call")
    max_recovery_retries: int = Field(default=3,
                                      description="Max call attempts to continue generation after the model hits its output token limit")
    base_retry_delay_ms: int = Field(default=500,
                                     description="Base delay in milliseconds before retrying an overloaded server")

    auto_compact: bool = Field(default=True, description="Enable automatic context compaction")
    compact_type: str = Field(default="summary", description="Context compaction method")
    compact_threshold: float | int = Field(default=0.5,
                                           description="Context usage that triggers compaction, resolved per current model at runtime. Values <= 1 are a fraction of the current context length; larger values are absolute token counts, capped to leave room for max output",
                                           gt=0)
    reserve_threshold: float | int = Field(default=0.1,
                                           description="Recent message tokens kept after compaction. Values <= 1 are a fraction of the current context length; larger values are absolute token counts",
                                           ge=0)
    summary_max_tokens: int = Field(default=20_000, description="Max tokens for the summary produced during compaction")

    enable_experimental_tools: bool = Field(default=False, description="Enable experimental features and tools")
    enable_defer_tools: bool = Field(default=False, description="Enable deferred tool loading")
    enable_defer_skills: bool = Field(default=False, description="Enable deferred skill loading")

    def resolve_compact_threshold(self, context_length: int, max_output: Optional[int]) -> int:
        """有效压缩阈值：<=1 按当前上下文长度比例解析 / 绝对值；上限给单次输出留空间——
        max_output 为空（无固定输出上限）时用 escalated_max_tokens，保证 输入 + 输出 < 上下文长度。"""
        headroom = max_output if max_output is not None else self.escalated_max_tokens
        return min(_scale_tokens(self.compact_threshold, context_length),
                   max(1, context_length - headroom))

    def resolve_reserve_threshold(self, context_length: int, compact_threshold: int) -> int:
        """有效保留预算：<=1 按当前上下文长度比例解析 / 绝对值，封顶有效压缩阈值。"""
        return min(_scale_tokens(self.reserve_threshold, context_length), compact_threshold)

    def clamp_max_tokens(self, requested: int, max_output: Optional[int]) -> int:
        """单次调用 max_tokens 钳制到模型固定最大输出；max_output 为空时不钳制（由 输入+输出<上下文 约束）。"""
        return int(requested) if max_output is None else max(1, min(int(requested), int(max_output)))


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
