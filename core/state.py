from pydantic import BaseModel, Field


class MCPState(BaseModel):
    mcp_config_file_path: str = Field(description="MCP Configuration File")
    mcp_connection_timeout: int = Field(description="MCP Connection Timeout")
    mcp_tool_call_timeout: int = Field(description="MCP Tool Call Timeout")


class ModelState(BaseModel):
    model: str = Field(description="Model Name")
    sub_model: str = Field(description="Sub Model Name")
    fallback_model: str = Field(description="Fallback Model Name")


class HarnessState(BaseModel):
    work_dir: str = Field(description="Working Directory")
    skill_dir: str = Field(description="Skills Directory")
    transcript_dir: str = Field(description="Transcript Directory")
    tool_results_dir: str = Field(description="Tool Results Directory")

    context_limit: int = Field(default=50_000, description="Context Limit")

    default_max_tokens: int = Field(default=8000, description="Default Max Tokens")
    escalated_max_tokens: int = Field(default=16_000, description="Escalated Max Tokens")
    max_retries: int = Field(default=3, description="Max Retries")
    max_recovery_retries: int = Field(default=2, description="Max Recovery Retries")
    max_consecutive: int = Field(default=2, description="Max Consecutive Retries")

    base_delay_ms: int = Field(default=500, description="Base Delay MS")

    keep_recent_tool_results: int = Field(default=3, description="Keep Recent Tool Results")

    continuation_prompt: str = "Continue from the previous response. Do not repeat completed work."

    rounds_since_todo: int = Field(default=0)
