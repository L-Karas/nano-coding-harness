import json
from pathlib import Path
from typing import Literal, Optional

import dotenv
from pydantic import BaseModel, Field

dotenv.load_dotenv()


def format_workdir_name(workdir: Path) -> str:
    """工作目录绝对路径 → 单层会话目录名：分隔符与盘符冒号转 `_`，空格转 `-`。

    例：``E:\\AI-Programs\\nano-harness`` → ``E__AI-Programs_nano-harness``。
    映射是有损的（``E:\\a_b`` 与 ``E:\\a/b`` 同名）；``workdir`` 须为绝对路径。
    """
    if not workdir.is_absolute():
        raise ValueError(f"workdir must be absolute: {workdir}")
    translation = str.maketrans({"\\": "_", "/": "_", ":": "_", " ": "-"})
    return str(workdir).translate(translation)


WORKDIR = Path.cwd()
HARNESS_DIR_NAME = ".nano-harness"

# 运行模式：dev = .nano-harness 位于项目目录（WORKDIR），prod = 位于 home 目录。
# 由入口解析后调用 configure_mode() 决定（见 core/cli.py）；裸导入时缺省 dev。
Mode = Literal["dev", "prod"]
DEFAULT_MODE: Mode = "dev"
MODE: Mode = DEFAULT_MODE


def _recompute_paths(mode: Mode) -> None:
    """按 mode 重算全部 .nano-harness 派生路径（模块级常量）。

    WORKDIR 不受 mode 影响：它始终是项目根（工具沙箱、系统提示词、@ 补全、会话目录键）。
    维护提示：新增路径常量必须同时加入下方 global 列表与 tests/test_mode.py::_PATH_NAMES，
    否则它会静默停在 dev 值。
    """
    global MODE, HARNESS_CONFIG_DIR, LOG_DIR, SKILL_DIR, EXTENSION_DIR, MEMORY_DIR, \
        TOOL_RESULTS_DIR, MCP_DIR, SESSION_ROOT_DIR, SESSION_DIR, HARNESS_SETTING_FILE, \
        SKILL_STATE_FILE, PROVIDER_AUTH_FILE, CUSTOM_PROVIDER_FILE, CUSTOM_MODEL_FILE, \
        SESSION_INDEX_FILE, MCP_CONFIG_FILE, CRON_TASK_FILE, PERMISSION_FILE
    MODE = mode
    root = (WORKDIR if mode == "dev" else Path.home()) / HARNESS_DIR_NAME
    HARNESS_CONFIG_DIR = root
    LOG_DIR = root / "log"
    SKILL_DIR = root / "skills"
    EXTENSION_DIR = root / "extensions"
    MEMORY_DIR = root / ".memory"
    TOOL_RESULTS_DIR = root / ".task_outputs" / "tool_results"
    MCP_DIR = root / ".mcp"
    SESSION_ROOT_DIR = root / ".session"
    SESSION_DIR = SESSION_ROOT_DIR / format_workdir_name(WORKDIR)
    HARNESS_SETTING_FILE = root / ".settings.json"
    SKILL_STATE_FILE = root / ".skills.json"
    PROVIDER_AUTH_FILE = root / ".auth.json"
    CUSTOM_PROVIDER_FILE = root / ".custom_providers.json"
    CUSTOM_MODEL_FILE = root / ".custom_models.json"
    SESSION_INDEX_FILE = SESSION_DIR / "session_index.jsonl"
    MCP_CONFIG_FILE = MCP_DIR / ".mcp.json"
    CRON_TASK_FILE = root / ".scheduled_tasks.json"
    PERMISSION_FILE = root / ".permissions.json"


_recompute_paths(DEFAULT_MODE)

GLOBAL_SKILLS_DIR = Path.home() / ".agents" / "skills"
PROJECT_SKILL_DIR = WORKDIR / ".agents" / "skills"

# 工具审批策略；下拉选项的取值即这里的字面量（顺序即表单顺序）
ApprovalMode = Literal["LLM", "Decision Model", "Manual", "Yolo"]
DEFAULT_APPROVAL_MODE: ApprovalMode = "Manual"
# 压缩阈值兜底：最多取窗口的 80%。
COMPACT_FALLBACK_PERCENT = 80


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
                                      description="Max tokens for escalated calls when the default limit is insufficient; also the output reserve used by the compaction threshold only after compact_trigger_tokens exceeds the 80% fallback")

    max_retries: int = Field(default=3, description="Max retries for a failed model call")
    max_recovery_retries: int = Field(default=3,
                                      description="Max call attempts to continue generation after the model hits its output token limit")
    base_retry_delay_ms: int = Field(default=500,
                                     description="Base delay in milliseconds before retrying an overloaded server")

    auto_compact: bool = Field(default=True, description="Enable automatic context compaction")
    compact_type: str = Field(default="summary", description="Context compaction method")
    compact_trigger_tokens: int = Field(default=500_000,
                                        description="Absolute token count of the context that triggers compaction; the effective threshold is resolved per model and capped at 80% of the model window (see docs/context-compaction.md)",
                                        gt=0)
    compact_reserve_tokens: int = Field(default=50_000,
                                        description="Absolute token budget of recent messages kept after compaction; capped by the effective trigger threshold",
                                        ge=0)
    summary_max_tokens: int = Field(default=20_000, description="Max tokens generated for the compaction summary")

    enable_experimental_tools: bool = Field(default=False, description="Enable experimental features and tools")
    enable_defer_tools: bool = Field(default=False, description="Enable deferred tool loading")
    enable_defer_skills: bool = Field(default=False, description="Enable deferred skill loading")

    def resolve_compact_trigger_tokens(self, context_length: int) -> int:
        """有效压缩阈值（绝对 token 数）；无可用上下文窗口（0）时返回 0。

        兜底 = max(1, context_length × 80%)：最终阈值不超过兜底（窗口过小时保底 1），
        防止用户配置过大。优先使用 compact_trigger_tokens；超过兜底时再尝试
        context_length - escalated_max_tokens；仍超过兜底时使用兜底。
        """
        if context_length <= 0:
            return 0
        # todo: escalated_max_tokens 接近或超过 context_length 时，候选值会塌到 1（每轮压缩）；
        # 后续评估极端配置下回落到 80% 兜底，或限制 escalated_max_tokens。
        # 下限 1：0 是“无可用窗口”的哨兵值，不能作为阈值返回。
        fallback_tokens = max(1, context_length * COMPACT_FALLBACK_PERCENT // 100)
        if self.compact_trigger_tokens <= fallback_tokens:
            return self.compact_trigger_tokens
        reserve_tokens = max(1, context_length - self.escalated_max_tokens)
        return min(fallback_tokens, reserve_tokens)

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


def configure_mode(mode: Mode) -> None:
    """切换运行模式：重算 .nano-harness 根目录与全部派生路径，并按新根重载 AgentConfig。

    必须在其他 core 模块导入前调用：那些模块 `from core.config import X` 是导入期值绑定，
    早于本调用导入就仍持有 dev 路径（导入顺序由 core/cli.py 与 tests/test_mode.py 保证/锁定）。
    """
    if mode not in ("dev", "prod"):
        raise ValueError(f"unknown mode: {mode!r}")
    if mode == MODE:
        return
    _recompute_paths(mode)
    # 保持 manager 实例身份（消费方可能在 configure_mode 前已持有引用），只换配置内容
    CONFIGMANAGER.config = AgentConfigManager.load_config().config
