import json
import threading
from functools import partial
from pathlib import Path
from typing import Optional, Any

from openai import OpenAI, Stream
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from core.config import HARNESS_SETTING_FILE, PROVIDER_AUTH_FILE


_MODEL_LIST_PATH: Path = Path(__file__).parent / "models.json"
_MODEL_LIST: dict[str, dict] = {}
_PROVIDER_AUTH: dict[str, Any] = {}
_HARNESS_SETTING: dict[str, Any] = {}


def _registry_models() -> None:
    global _MODEL_LIST, _PROVIDER_AUTH, _HARNESS_SETTING

    if not _MODEL_LIST_PATH.exists():
        raise Exception(f"{_MODEL_LIST_PATH} does not exist")

    try:
        _MODEL_LIST = {m.lower(): i for m, i in json.loads(_MODEL_LIST_PATH.read_text(encoding="utf-8")).items()}
        _PROVIDER_AUTH = json.loads(PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
        _HARNESS_SETTING = json.loads(HARNESS_SETTING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise e


_registry_models()


def _update_default_settings(provider: str, model: str, thinking_level: str = "max") -> None:
    global _HARNESS_SETTING

    _HARNESS_SETTING = _HARNESS_SETTING | {
        "default_provider": provider,
        "default_model": model,
        "default_thinking_level": thinking_level,
    }
    try:
        HARNESS_SETTING_FILE.write_text(json.dumps(_HARNESS_SETTING, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        raise Exception(f"Update default provider, model, thinking level error. Error: {e}")


def get_provider_list() -> list[tuple[str, str]]:
    global _MODEL_LIST

    provider_list = []
    for provider in _MODEL_LIST.keys():
        if provider not in _PROVIDER_AUTH:
            provider_list.append((provider, f"[○ unconfigured]"))
        else:
            provider_list.append((provider, f"[● configured]"))

    return provider_list


def configure_provider(provider: str, api_key: str) -> None:
    global _PROVIDER_AUTH

    try:
        _PROVIDER_AUTH = _PROVIDER_AUTH | {provider.lower(): {"api_key": api_key}}
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        raise e


# todo: 若取消配置当前使用的模型提供商，需要同步更新 client 和 harness settings
def unconfigure_provider(provider: str) -> None:
    global _PROVIDER_AUTH
    try:
        _PROVIDER_AUTH.pop(provider.lower())
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        raise Exception(f"Unconfigure provider error. Error: {e}")


def get_model_list() -> list[tuple[str, str]]:
    global _MODEL_LIST, _PROVIDER_AUTH

    model_list = []
    for configured_provider in _PROVIDER_AUTH.keys():
        for model_name in _MODEL_LIST[configured_provider]["model_list"].keys():
            model_list.append((model_name, f"[{configured_provider}]"))

    return model_list


class ModelClient:

    def __init__(self) -> None:
        self.current_provider = ""
        self.current_model = ""
        self.current_model_thinking = True
        self.current_thinking_level = ""
        self.current_client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self):
        global _HARNESS_SETTING
        try:
            self.set_model_client(
                _HARNESS_SETTING["default_provider"],
                _HARNESS_SETTING["default_model"],
                _HARNESS_SETTING["default_thinking_level"]
            )
        except Exception as e:
            raise e

    def _init_model_client(self):
        global _MODEL_LIST, _PROVIDER_AUTH

        if not self.current_provider or not self.current_model:
            raise Exception(f"Please set provider and model")

        self.current_client = OpenAI(
            api_key=_PROVIDER_AUTH.get(self.current_provider).get("api_key"),
            base_url=_MODEL_LIST.get(self.current_provider).get("base_url"),
        )

    def set_thinking_level(self, thinking_level: str) -> None:
        # thinking_level: map 键(UI 档位)或 ""(关闭思考)。档位值为 false = 该模型不支持此档：
        # thinking 照开、请求时不传 reasoning_effort。未知键(如旧版误存的 API 值)按关闭处理，重启不得 KeyError。
        model_cfg = _MODEL_LIST[self.current_provider]["model_list"][self.current_model]
        supported = thinking_level in model_cfg["thinking_level_map"]
        self.current_thinking_level = thinking_level if supported else ""
        self.current_model_thinking = model_cfg["thinking"]

    def set_model_client(self, provider: str, model: str, thinking_level: str = "max") -> None:
        self.current_provider = provider.lower()
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
                    _MODEL_LIST[self.current_provider]["model_list"][self.current_model]["thinking_level_map"][self.current_thinking_level]
                if effort:
                    kwargs["reasoning_effort"] = effort
        return kwargs

    def get_model_client(self) -> OpenAI:
        if not self.current_provider or not self.current_model:
            raise Exception(f"Please set provider and model")

        return partial(
            self.current_client.chat.completions.create,
            model=self.current_model,
            **self._request_kwargs()
        )

    # todo: 待完善，暂未使用
    def call(self, messages: list[dict[str, Any]], **kwargs) -> ChatCompletion | Stream[ChatCompletionChunk]:
        return self.current_client.chat.completions.create(
            model=self.current_model,
            messages=messages,
            **kwargs | self._request_kwargs()
        )


# ---------- 进程内共享 ModelClient ----------
# main agent / sub-agent / 上下文压缩 / teammate 等所有 LLM 调用统一走同一实例：
# 模型配置（.harness/.setting.json 的 default_provider / default_model / thinking）只有单一来源。
# 惰性构建：首次实际调用时才读取配置并实例化，import 阶段无副作用（测试/演示环境可安全导入）。

_CLIENT: Optional["ModelClient"] = None
_CLIENT_LOCK = threading.Lock()


def shared_model_client() -> ModelClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:  # teammate 线程与主 agent 线程可能同时首次调用，防重复实例化
            if _CLIENT is None:
                _CLIENT = ModelClient()
    return _CLIENT