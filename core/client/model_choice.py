"""子代理的显式模型选择：模型引用与思考档位必须成对给出，并在注册表上校验。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.client import registry

ThinkingLevel = Literal["minimal", "low", "medium", "high", "max"]


class ModelChoice(BaseModel):
    """一次子代理运行的显式模型选择：模型引用与思考档位必须成对给出。"""
    model_config = ConfigDict(extra="forbid")
    model: str = Field(
        description="Full model reference in the form 'provider:model' (case-sensitive).")
    thinking_level: ThinkingLevel = Field(
        description="Reasoning effort for this model: minimal / low / medium / high / max."
    )

    @field_validator("model")
    @classmethod
    def _require_provider_and_model(cls, value: str) -> str:
        """要求 ``provider:model`` 形式（模型名本身可以再含冒号）。

        Args:
            value: 模型引用。

        Returns:
            去掉首尾空白后的引用。

        Raises:
            ValueError: 缺少 provider 或模型名。
        """
        ref = value.strip()
        provider, sep, model_name = ref.partition(":")
        if not sep or not provider or not model_name:
            raise ValueError("model must be in the form 'provider:model'")
        return ref

    @property
    def provider(self) -> str:
        """引用中的 provider 段。

        Returns:
            provider 名称。
        """
        return self.model.partition(":")[0]

    @property
    def model_name(self) -> str:
        """引用中的模型名段（可含冒号）。

        Returns:
            模型名称。
        """
        return self.model.partition(":")[2]

    def cache_key(self) -> tuple[str, str, str]:
        """按模型配置区分 client 的缓存键。

        Returns:
            ``(provider, model, thinking_level)``。
        """
        return self.provider, self.model_name, self.thinking_level


def validate_model_choice(choice: ModelChoice) -> None:
    """校验显式模型选择：provider / 模型必须已注册，provider 必须已配置非空 API key。

    Args:
        choice: 子代理要使用的模型与档位。

    Raises:
        ValueError: provider 未知、模型未注册或 provider 没有可用 API key。
    """
    provider, model = choice.provider, choice.model_name
    if provider not in registry._MODEL_LIST:
        raise ValueError(f"unknown provider '{provider}' in model '{choice.model}'")
    model_list = registry._MODEL_LIST[provider].get("model_list", {})
    if model not in model_list:
        available = ", ".join(f"{provider}:{name}" for name in model_list) or "(none)"
        raise ValueError(
            f"unknown model '{choice.model}' for provider '{provider}'; "
            f"available models under '{provider}': {available}"
        )
    if not registry.is_provider_available(provider):
        raise ValueError(f"provider '{provider}' has no API key configured; run /provider first")
