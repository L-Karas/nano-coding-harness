from core.client import list_available_models
from core.config import CONFIGMANAGER
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool


class ListModels(BaseTool):
    """List the models available in this session (only providers with configured API keys), including each model's vision support, thinking support, thinking levels, context window and max output. Use it to choose the model_choice for spawn_subagent."""
    agent_type: set = {"main"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        """渲染默认模型与可用模型清单。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            供主代理选择子代理模型的多行文本。
        """
        config = CONFIGMANAGER.config
        default_model = config.default_model or "(unset)"
        if config.default_sub_model:
            default_sub = config.default_sub_model
            default_sub_thinking = config.default_sub_model_thinking_level
        else:
            default_sub = default_model
            default_sub_thinking = config.default_thinking_level

        lines = [
            f"Default model: {default_model} @ {config.default_thinking_level}",
            f"Default sub model: {default_sub} @ {default_sub_thinking}",
            "",
            "Available models (provider:model | vision | thinking | thinking_levels | context | max output):",
        ]
        available = list_available_models()
        if not available:
            lines.append("  (none: configure a provider API key with /provider first)")
        for item in available:
            if not item.thinking:
                thinking_levels = "none"
            elif item.thinking_levels:
                thinking_levels = ",".join(item.thinking_levels)
            else:
                thinking_levels = "none (thinking on)"  # 思考恒开但不支持调档，与 thinking=no 区分
            thinking = "yes" if item.thinking else "no"
            vision = "yes" if item.vision else "no"
            max_output = "none" if item.max_output is None else item.max_output
            lines.append(
                f"  {item.ref} | vision={vision} | thinking={thinking} | "
                f"thinking_levels={thinking_levels} | "
                f"context={item.context_length} | max_output={max_output}"
            )
        return "\n".join(lines)
