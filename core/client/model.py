import json
from functools import partial
from pathlib import Path
from typing import Optional, Any

from openai import OpenAI, AsyncOpenAI

from core.config import HARNESS_SETTING_FILE, PROVIDER_AUTH_FILE, CUSTOM_MODEL_FILE, CUSTOM_PROVIDER_FILE
from core.log import get_logger

_MODEL_LIST_PATH: Path = Path(__file__).parent / "models.json"
_MODEL_LIST: dict[str, dict] = {}
_PROVIDER_AUTH: dict[str, dict] = {}
_CUSTOM_MODEL_LIST: dict[str, dict] = {}
_CUSTOM_PROVIDER_AUTH: dict[str, dict] = {}
_HARNESS_SETTING: dict[str, Any] = {}
_LOGGER = get_logger(__name__)


def _registry_custom_models() -> tuple[dict, dict]:
    """
    Registry custom providers and models.

    Returns:
        tuple(model list, provider list)
    """
    global _CUSTOM_MODEL_LIST, _CUSTOM_PROVIDER_AUTH

    model_list, provider_list = {}, {}
    if CUSTOM_MODEL_FILE.exists() and CUSTOM_PROVIDER_FILE.exists():
        _CUSTOM_PROVIDER_AUTH = {
            p: i for p, i in json.loads(CUSTOM_PROVIDER_FILE.read_text(encoding="utf-8")).items()
        }
        _CUSTOM_MODEL_LIST = {
            m: i for m, i in json.loads(CUSTOM_MODEL_FILE.read_text(encoding="utf-8")).items()
        }
        for provider, item in _CUSTOM_PROVIDER_AUTH.items():
            provider_list[provider] = {
                "api_key": item["api_key"],
            }
            model_list[provider] = {
                "base_url": item["base_url"],
                "model_list": _CUSTOM_MODEL_LIST.get(provider, {}).get("model_list", {}),
            }

    return model_list, provider_list


def _registry_models() -> None:
    """Registry all providers and models"""
    global _MODEL_LIST, _PROVIDER_AUTH, _HARNESS_SETTING

    if not _MODEL_LIST_PATH.exists():
        _LOGGER.error(f"[Registry Error] {_MODEL_LIST_PATH} does not exist")
        raise Exception(f"{_MODEL_LIST_PATH} does not exist")

    try:
        model_list, provider_list = _registry_custom_models()
        _MODEL_LIST = {provider: item for provider, item in
                       json.loads(_MODEL_LIST_PATH.read_text(encoding="utf-8")).items()}
        _PROVIDER_AUTH = json.loads(PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
        _MODEL_LIST = _MODEL_LIST | model_list
        _PROVIDER_AUTH = _PROVIDER_AUTH | provider_list
        _HARNESS_SETTING = json.loads(HARNESS_SETTING_FILE.read_text(encoding="utf-8"))
        _LOGGER.info(f"[Registry Models List] {_MODEL_LIST}]")
    except Exception as e:
        _LOGGER.error(f"[Loading Model Error] {e}")
        raise e


_registry_models()


def _update_default_settings(provider: str, model: str, thinking_level: str = "max") -> None:
    """When update model or thinking level, update default provider, model and thinking level."""
    global _HARNESS_SETTING

    _HARNESS_SETTING = _HARNESS_SETTING | {
        "default_provider": provider,
        "default_model": model,
        "default_thinking_level": thinking_level,
    }
    try:
        HARNESS_SETTING_FILE.write_text(json.dumps(_HARNESS_SETTING, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        _LOGGER.error(f"[Update Default Settings Error] {e}")
        raise Exception(f"Update default provider, model, thinking level error. Error: {e}")


def get_provider_list(custom_provider: bool = False) -> list[tuple[str, bool]]:
    """
    Get provider list for tui
    Args:
        custom_provider: If True, only get custom providers. Else, get builtin providers.

    Returns:
        list tuple [(provider name, is_configured)...]
    """
    global _MODEL_LIST

    if not custom_provider:
        return [(provider, provider in _PROVIDER_AUTH)
                for provider in _MODEL_LIST if provider not in _CUSTOM_PROVIDER_AUTH]
    else:
        return [(provider, provider in _PROVIDER_AUTH)
                for provider in _MODEL_LIST if provider in _CUSTOM_PROVIDER_AUTH]


def configure_provider(
        provider: str,
        api_key: str
) -> None:
    """Configure provider and api_key"""
    global _PROVIDER_AUTH

    try:
        _PROVIDER_AUTH = _PROVIDER_AUTH | {provider: {"api_key": api_key}}
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        _LOGGER.exception(f"[Configure Provider Error] {e}")
        raise e


def unconfigure_provider(provider: str) -> None:
    """删除某 provider 的 API Key 配置（幂等：未配置 / 已删该 provider 时为空操作）。

    删除的恰是当前默认提供商时：先清空 harness 默认设置并重建共享 client，再移除 key。
    顺序保证任一步落盘失败都留"设置已清、key 残留"的可自愈状态（重试时 default_provider
    已空即跳过 settings 分支，不会留"默认配置指向已删 key"的坏状态）。"""
    global _PROVIDER_AUTH, _CUSTOM_PROVIDER_AUTH, _HARNESS_SETTING
    try:
        if provider not in _PROVIDER_AUTH:
            return  # 幂等 no-op：未配置 / 已删（UI 对未配置行或重复按 Delete 都安全）
        _PROVIDER_AUTH.pop(provider)
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        _LOGGER.error(f"[Unconfigure Provider Error] {e}")
        raise Exception(f"Unconfigure provider error. Error: {e}")
    finally:
        if provider == _HARNESS_SETTING.get("default_provider"):
            _update_default_settings(provider="", model="")
            shared_model_client()._init_client()  # 复位内存中持有已删 key 的 client（见 _init_client 空默认分支）


def get_model_list(custom_model: bool = False) -> list[tuple[str, str]]:
    """
    Get model list for tui
    Args:
        custom_model: If True, only get custom models. Else, get all models.

    Returns:
        model list [(model name, [provider name])...]
    """
    global _MODEL_LIST, _PROVIDER_AUTH

    providers = _CUSTOM_PROVIDER_AUTH if custom_model else _PROVIDER_AUTH
    model_list = []
    for configured_provider in providers:
        for model_name in _MODEL_LIST.get(configured_provider, {}).get("model_list", {}):
            model_list.append((model_name, f"[{configured_provider}]"))

    return model_list


def login_provider(
        provider: str,
        base_url: str,
        api_key: str = ""
) -> bool:
    """Login a custom provider"""
    if provider in _PROVIDER_AUTH:
        _LOGGER.error(f"[Login Provider Error] {provider} exist")
        return False
    _CUSTOM_PROVIDER_AUTH[provider] = {"base_url": base_url, "api_key": api_key}
    _CUSTOM_MODEL_LIST[provider] = {
        "base_url": base_url,
        "api_key": api_key,
        "model_list": {},
    }
    try:
        with open(CUSTOM_PROVIDER_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_PROVIDER_AUTH, ensure_ascii=False, indent=4))
        with open(CUSTOM_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_MODEL_LIST, ensure_ascii=False, indent=4))
        # update provider and model list
        _PROVIDER_AUTH[provider] = {"api_key": api_key}
        _MODEL_LIST[provider] = {"base_url": base_url, "model_list": {}}
        _LOGGER.info(f"[Login Provider] login {provider} success")
    except Exception as e:
        _LOGGER.error(f"[Login Provider Error] {e}")
        return False

    return True


def login_model(
        provider: str,
        model: str,
        thinking: bool = True,
        vision: bool = True,
        context_length: int = 0,
        max_output: Optional[int] = None
) -> bool:
    """Login a custom model"""
    if provider not in _PROVIDER_AUTH:
        _LOGGER.error(f"[Login Provider Error] {provider} does not exist")
        return False
    _CUSTOM_MODEL_LIST[provider]["model_list"][model] = {
        "thinking": thinking,
        "vision": vision,
        "thinking_level_map": {
            "minimal": "minimal",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "max": "max",
        },
        "context_length": context_length,
        "max_output": max_output,
    }
    try:
        with open(CUSTOM_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_MODEL_LIST, ensure_ascii=False, indent=4))
        _MODEL_LIST[provider]["model_list"] = _CUSTOM_MODEL_LIST[provider]["model_list"]
        _LOGGER.info(f"[Login Model] Login {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Login Model Error] {e}")
        return False
    return True


def logout_provider(provider: str) -> bool:
    """Logout a custom provider"""
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Logout Provider Error] {provider} does not exist")
        return False

    try:
        # update custom providers and models
        _CUSTOM_PROVIDER_AUTH.pop(provider)
        _CUSTOM_MODEL_LIST.pop(provider)
        with open(CUSTOM_PROVIDER_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_PROVIDER_AUTH, ensure_ascii=False, indent=4))
        with open(CUSTOM_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_MODEL_LIST, ensure_ascii=False, indent=4))
        _PROVIDER_AUTH.pop(provider)
        _MODEL_LIST.pop(provider)
        # update default setting if provider is default_provider
        if provider == _HARNESS_SETTING.get("default_provider", ""):
            _update_default_settings(provider="", model="")
            # update client
            shared_model_client()._init_client()
        _LOGGER.info(f"[Logout Provider] logout {provider} success")
    except Exception as e:
        _LOGGER.error(f"[Logout Provider Error] {e}")
        return False
    return True


def logout_model(provider: str, model: str) -> bool:
    """Logout a custom model"""
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Logout Provider Error] {provider} does not exist")
        return False
    if model not in _CUSTOM_MODEL_LIST[provider]["model_list"]:
        _LOGGER.error(f"[Logout Model Error] {model} does not exist")
        return False

    try:
        # update custom models
        _CUSTOM_MODEL_LIST[provider]["model_list"].pop(model)
        with open(CUSTOM_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(_CUSTOM_MODEL_LIST, ensure_ascii=False, indent=4))
        # update setting if model is default_model
        if model == _HARNESS_SETTING.get("default_model", ""):
            _update_default_settings(provider="", model="")
            # update client
            shared_model_client()._init_client()
        _LOGGER.info(f"[Logout Model] Logout {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Logout Model Error] {e}")
        return False

    return True


class ModelClient:

    def __init__(self) -> None:
        self.current_provider = ""
        self.current_model = ""
        self.current_model_thinking = True
        self.current_thinking_level = ""
        self.current_client: Optional[OpenAI] = None
        self.current_client_async: Optional[AsyncOpenAI] = None
        self._init_client()

    def load_context_length(self) -> int:
        """加载当前模型上下文长度，未选择模型时返回 0。"""
        if not self.current_model:
            return 0
        return _MODEL_LIST[self.current_provider]["model_list"][self.current_model]["context_length"]

    def _init_client(self):
        global _HARNESS_SETTING
        # 默认配置为空（从未配置 / 默认提供商刚被 unconfigure_provider 清除）：
        # 留空不抛，等 set_model_client 显式配置（/model 指令即依赖此路径修复空默认状态）。
        # 曾直接 set_model_client("", "") → _MODEL_LIST[""] KeyError('')，
        # str 显示为 ''，shared_model_client 单例永远建不起来。
        provider = _HARNESS_SETTING.get("default_provider", "")
        model = _HARNESS_SETTING.get("default_model", "")
        if not provider or not model:
            # 反配置默认提供商后，旧 client 不得继续持有已删 key（否则请求仍带已删凭证发出、
            # 页脚仍显示已删模型）：显式复位，get_model_client 走 "Please set provider and model"
            self.current_provider = ""
            self.current_model = ""
            self.current_thinking_level = ""
            self.current_model_thinking = False
            self.current_client = None
            self.current_client_async = None
            return
        self.set_model_client(provider, model, _HARNESS_SETTING.get("default_thinking_level", "max"))

    def _init_model_client(self):
        global _MODEL_LIST, _PROVIDER_AUTH

        if not self.current_provider or not self.current_model:
            _LOGGER.info("Please set provider and model")
            raise Exception("Please set provider and model")  # 文案对齐 get_model_client 的同款提示

        self.current_client = OpenAI(
            api_key=_PROVIDER_AUTH.get(self.current_provider).get("api_key"),
            base_url=_MODEL_LIST.get(self.current_provider).get("base_url"),
        )
        self.current_client_async = AsyncOpenAI(
            api_key=_PROVIDER_AUTH.get(self.current_provider).get("api_key"),
            base_url=_MODEL_LIST.get(self.current_provider).get("base_url"),
        )

    def set_thinking_level(self, thinking_level: str) -> None:
        # thinking_level: map 键(UI 档位)或 ""(关闭思考)。档位值为 false = 该模型不支持此档：
        # thinking 照开、请求时不传 reasoning_effort。未知键(如旧版误存的 API 值)按关闭处理，重启不得 KeyError。
        # todo: 接收的参数始终不为空
        if not thinking_level:
            self.current_thinking_level = ""
            self.current_model_thinking = False  # 空档位 = 关闭思考（含默认配置为空时的防御，不再查 model_cfg）
            return

        model_cfg = _MODEL_LIST[self.current_provider]["model_list"][self.current_model]
        supported = thinking_level in model_cfg["thinking_level_map"]
        self.current_thinking_level = thinking_level if supported else ""
        self.current_model_thinking = model_cfg["thinking"]
        _update_default_settings(self.current_provider, self.current_model, thinking_level)

    def set_model_client(self, provider: str, model: str, thinking_level: str = "max") -> None:
        self.current_provider = provider
        self.current_model = model
        self.set_thinking_level(thinking_level)
        _update_default_settings(self.current_provider, self.current_model, thinking_level)
        self._init_model_client()

    def _request_kwargs(self) -> dict[str, Any]:
        # thinking 开启才带 extra_body；档位 map 值为 false 或已关思考时不传 reasoning_effort
        kwargs: dict[str, Any] = {}
        if self.current_model_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            if self.current_thinking_level:
                effort = \
                    _MODEL_LIST[self.current_provider]["model_list"][self.current_model]["thinking_level_map"][
                        self.current_thinking_level]
                if effort:
                    kwargs["reasoning_effort"] = effort
        return kwargs

    def get_model_client(self, async_client: bool = False) -> OpenAI | AsyncOpenAI:
        if not self.current_provider or not self.current_model:
            _LOGGER.info("Please set provider and model")
            raise Exception(f"Please set provider and model")

        request_args = self._request_kwargs()
        _LOGGER.info(f"Model: {self.current_model}({self.current_provider}), "
                     f"request_args: {request_args}, async_client: {async_client}")
        if async_client:
            return partial(
                self.current_client_async.chat.completions.create,
                model=self.current_model,
                **request_args
            )
        return partial(
            self.current_client.chat.completions.create,
            model=self.current_model,
            **request_args
        )


# ---------- 进程内共享 ModelClient ----------
# main agent / sub-agent / 上下文压缩 / teammate 等所有 LLM 调用统一走同一实例：
# 模型配置（.harness/.setting.json 的 default_provider / default_model / thinking）只有单一来源。
# 惰性构建：首次实际调用时才读取配置并实例化，import 阶段无副作用（测试/演示环境可安全导入）。
# 无需加锁：主 agent 与 cron 的 agent 回合已被 AGENT_LOCK 串行，且每轮先建 client 再执行 tool；
# teammate / sub-agent 只能在主回合中途被 spawn，届时 _CLIENT 必然已建立，不存在并发首次实例化。

_CLIENT: Optional["ModelClient"] = None


def shared_model_client() -> ModelClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ModelClient()
    return _CLIENT
