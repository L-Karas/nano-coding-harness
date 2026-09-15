import json
from functools import partial
from pathlib import Path
from typing import Optional, Any

from openai import OpenAI, Stream, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from core.config import HARNESS_SETTING_FILE, PROVIDER_AUTH_FILE
from core.log import get_logger

_MODEL_LIST_PATH: Path = Path(__file__).parent / "models.json"
_MODEL_LIST: dict[str, dict] = {}
_PROVIDER_AUTH: dict[str, Any] = {}
_HARNESS_SETTING: dict[str, Any] = {}
_LOGGER = get_logger(__name__)


def _registry_models() -> None:
    global _MODEL_LIST, _PROVIDER_AUTH, _HARNESS_SETTING

    if not _MODEL_LIST_PATH.exists():
        _LOGGER.error(f"[Registry Error] {_MODEL_LIST_PATH} does not exist")
        raise Exception(f"{_MODEL_LIST_PATH} does not exist")

    try:
        _MODEL_LIST = {m.lower(): i for m, i in json.loads(_MODEL_LIST_PATH.read_text(encoding="utf-8")).items()}
        _PROVIDER_AUTH = json.loads(PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
        _HARNESS_SETTING = json.loads(HARNESS_SETTING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        _LOGGER.error(f"[Loading Model Error] {e}")
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
        _LOGGER.error(f"[Update Default Settings Error] {e}")
        raise Exception(f"Update default provider, model, thinking level error. Error: {e}")


def get_provider_list() -> list[tuple[str, bool]]:
    """已注册 provider 及配置状态（True = 已配置 API Key）。
    只回传结构化状态，展示文案由 UI 层负责（UI 需按状态着色，解析展示串易碎）。"""
    global _MODEL_LIST

    return [(provider, provider in _PROVIDER_AUTH) for provider in _MODEL_LIST]


def configure_provider(provider: str, api_key: str) -> None:
    global _PROVIDER_AUTH

    try:
        _PROVIDER_AUTH = _PROVIDER_AUTH | {provider.lower(): {"api_key": api_key}}
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        _LOGGER.error(f"[Configure Provider Error] {e}")
        raise e


def unconfigure_provider(provider: str) -> None:
    """删除某 provider 的 API Key 配置（幂等：未配置 / 已删该 provider 时为空操作）。

    删除的恰是当前默认提供商时：先清空 harness 默认设置并重建共享 client，再移除 key。
    顺序保证任一步落盘失败都留"设置已清、key 残留"的可自愈状态（重试时 default_provider
    已空即跳过 settings 分支，不会留"默认配置指向已删 key"的坏状态）。"""
    global _PROVIDER_AUTH, _HARNESS_SETTING
    try:
        provider = provider.lower()
        if provider not in _PROVIDER_AUTH:
            return  # 幂等 no-op：未配置 / 已删（UI 对未配置行或重复按 Delete 都安全）
        if provider == _HARNESS_SETTING.get("default_provider"):  # .get：设置文件可无此键（从未配置/手改），直接索引会 KeyError
            _HARNESS_SETTING = _HARNESS_SETTING | {
                "default_provider": "",
                "default_model": "",
                "default_thinking_level": "max",
            }
            HARNESS_SETTING_FILE.write_text(json.dumps(_HARNESS_SETTING, ensure_ascii=False, indent=4),
                                            encoding="utf-8")
            shared_model_client()._init_client()  # 复位内存中持有已删 key 的 client（见 _init_client 空默认分支）
        _PROVIDER_AUTH.pop(provider)
        PROVIDER_AUTH_FILE.write_text(json.dumps(_PROVIDER_AUTH, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as e:
        _LOGGER.error(f"[Unconfigure Provider Error] {e}")
        raise Exception(f"Unconfigure provider error. Error: {e}")


def get_model_list() -> list[tuple[str, str]]:
    global _MODEL_LIST, _PROVIDER_AUTH

    model_list = []
    for configured_provider in _PROVIDER_AUTH.keys():
        for model_name in _MODEL_LIST[configured_provider]["model_list"].keys():
            model_list.append((model_name, f"[{configured_provider.title()}]"))

    return model_list


class ModelClient:

    def __init__(self) -> None:
        self.current_provider = ""
        self.current_model = ""
        self.current_model_thinking = True
        self.current_thinking_level = ""
        self.current_client: Optional[OpenAI] = None
        self.current_client_async: Optional[AsyncOpenAI] = None
        self._init_client()

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
                    _MODEL_LIST[self.current_provider]["model_list"][self.current_model]["thinking_level_map"][
                        self.current_thinking_level]
                if effort:
                    kwargs["reasoning_effort"] = effort
        return kwargs

    def get_model_client(self, async_client: bool = False) -> OpenAI | AsyncOpenAI:
        if not self.current_provider or not self.current_model:
            _LOGGER.info("Please set provider and model")
            raise Exception(f"Please set provider and model")

        if async_client:
            return partial(
                self.current_client_async.chat.completions.create,
                model=self.current_model,
                **self._request_kwargs()
            )
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
# 无需加锁：主 agent 与 cron 的 agent 回合已被 AGENT_LOCK 串行，且每轮先建 client 再执行 tool；
# teammate / sub-agent 只能在主回合中途被 spawn，届时 _CLIENT 必然已建立，不存在并发首次实例化。

_CLIENT: Optional["ModelClient"] = None


def shared_model_client() -> ModelClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ModelClient()
    return _CLIENT
