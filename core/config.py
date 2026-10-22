import json
from pathlib import Path
from typing import Literal, Optional

import dotenv
from pydantic import BaseModel, Field

dotenv.load_dotenv()

WORKDIR = Path.cwd()
HARNESS_CONFIG_DIR = WORKDIR / ".harness"
LOG_DIR = HARNESS_CONFIG_DIR / "log"
SKILL_DIR = HARNESS_CONFIG_DIR / "skills"
EXTENSION_DIR = HARNESS_CONFIG_DIR / "extensions"
MEMORY_DIR = HARNESS_CONFIG_DIR / ".memory"
SESSION_DIR = HARNESS_CONFIG_DIR / ".session"
TOOL_RESULTS_DIR = HARNESS_CONFIG_DIR / ".task_outputs" / "tool_results"
MCP_DIR = HARNESS_CONFIG_DIR / ".mcp"

HARNESS_SETTING_FILE = HARNESS_CONFIG_DIR / ".settings.json"
PROVIDER_AUTH_FILE = HARNESS_CONFIG_DIR / ".auth.json"
CUSTOM_PROVIDER_FILE = HARNESS_CONFIG_DIR / ".custom_providers.json"
CUSTOM_MODEL_FILE = HARNESS_CONFIG_DIR / ".custom_models.json"
SESSION_INDEX_FILE = HARNESS_CONFIG_DIR / ".session" / "session_index.jsonl"
MCP_CONFIG_FILE = MCP_DIR / ".mcp.json"
CRON_TASK_FILE = HARNESS_CONFIG_DIR / ".scheduled_tasks.json"
PERMISSION_FILE = HARNESS_CONFIG_DIR / ".permissions.json"

GLOBAL_SKILLS_DIR = Path.home() / ".agents" / "skills"
PROJECT_SKILL_DIR = WORKDIR / ".agents" / "skills"

# 工具审批策略；下拉选项的取值即这里的字面量（顺序即表单顺序）
ApprovalMode = Literal["LLM", "Decision Model", "Manual", "Yolo"]
DEFAULT_APPROVAL_MODE: ApprovalMode = "Manual"


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

    approval_mode: ApprovalMode = Field(
        default=DEFAULT_APPROVAL_MODE,
        description="Tool approval mode. Manual asks on destructive commands / outside paths; "
                    "Yolo only runs the floor check and skips everything else (trusts LLM output: "
                    "rules, extension votes and the default layer are ignored); LLM / Decision Model "
                    "are not implemented and fall back to Manual")

    default_max_tokens: int = Field(default=16_000, description="Max tokens per default model call")
    escalated_max_tokens: int = Field(default=32_000,
                                      description="Max tokens for escalated calls when the default limit is insufficient; also the tokens compaction reserves for output when the model declares no fixed max output")

    max_retries: int = Field(default=3, description="Max retries for a failed model call")
    max_recovery_retries: int = Field(default=3,
                                      description="Max call attempts to continue generation after the model hits its output token limit")
    base_retry_delay_ms: int = Field(default=500,
                                     description="Base delay in milliseconds before retrying an overloaded server")

    auto_compact: bool = Field(default=True, description="Enable automatic context compaction")
    compact_type: str = Field(default="summary", description="Context compaction method")
    compact_trigger_tokens: int = Field(default=500_000,
                                        description="Absolute token count of the context that triggers compaction; capped per model to leave room for max output",
                                        gt=0)
    compact_reserve_tokens: int = Field(default=50_000,
                                        description="Absolute token budget of recent messages kept after compaction; capped by the effective trigger threshold",
                                        ge=0)
    summary_max_tokens: int = Field(default=20_000, description="Max tokens generated for the compaction summary")

    enable_experimental_tools: bool = Field(default=False, description="Enable experimental features and tools")
    enable_defer_tools: bool = Field(default=False, description="Enable deferred tool loading")
    enable_defer_skills: bool = Field(default=False, description="Enable deferred skill loading")

    def resolve_compact_trigger_tokens(self, context_length: int, max_output: Optional[int]) -> int:
        """有效压缩阈值（绝对 token 数）；无可用上下文窗口（0）时返回 0；上限给单次输出留空间——
        max_output 为空（无固定输出上限）时用 escalated_max_tokens，保证 输入 + 输出 < 上下文长度。"""
        if context_length <= 0:
            return 0
        # todo: max output 与 context length 一样时，会出现问题
        output_reserve_tokens = max_output if max_output is not None else self.escalated_max_tokens
        return min(self.compact_trigger_tokens, max(1, context_length - output_reserve_tokens))

    def resolve_compact_reserve_tokens(self, compact_trigger_tokens: int) -> int:
        """有效保留预算（绝对 token 数），封顶有效压缩阈值。"""
        return min(self.compact_reserve_tokens, compact_trigger_tokens)

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

        raw = json.loads(HARNESS_SETTING_FILE.read_text(encoding="utf-8"))
        return cls(config=AgentConfig.model_validate(raw))


CONFIGMANAGER = AgentConfigManager.load_config()
