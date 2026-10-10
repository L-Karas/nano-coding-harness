"""模型注册表（内置 + 自定义）与运行时 ModelClient。

- 注册表：内置 ``models.json`` / ``.auth.json`` 与自定义 ``.custom_providers.json`` /
  ``.custom_models.json`` 的读写。运行时以 ``_MODEL_LIST``（内置 + 自定义合并，模型真源）
  与 ``_PROVIDER_AUTH``（已配置 key 的 provider）为准；``_CUSTOM_PROVIDER_AUTH`` 只用于
  区分自定义 / 内置 provider，两个自定义 JSON 均由它派生，不另存一份运行时副本。
- ModelClient：按当前 provider / model / 思考档位构造 OpenAI 兼容请求；进程内共享实例见
  ``shared_model_client`` 与 ``shared_sub_model_client``。
"""

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI, OpenAI

from core.config import CONFIGMANAGER, CUSTOM_MODEL_FILE, CUSTOM_PROVIDER_FILE, PROVIDER_AUTH_FILE
from core.log import get_logger

_MODEL_LIST_PATH: Path = Path(__file__).parent / "models.json"

_MODEL_LIST: dict[str, dict] = {}  # provider -> {"base_url", "model_list": {model: cfg}}
_PROVIDER_AUTH: dict[str, dict] = {}  # provider -> {"api_key"}，仅已配置的 provider
_CUSTOM_PROVIDER_AUTH: dict[str, dict] = {}  # provider -> {"base_url", "api_key"}，全部自定义 provider

_LOGGER = get_logger(__name__)


# ---------- 注册表：加载 / 持久化 / 自定义 provider·model 管理 ----------


def _write_json(path: Path, data: dict) -> None:
    """统一 JSON 落盘格式（UTF-8 / 中文不转义 / 4 空格缩进）；异常由调用方处理。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def _save_custom_models() -> None:
    """持久化自定义模型：provider 段（base_url / api_key）取自注册信息，模型取自 _MODEL_LIST。"""
    models = {
        provider: {
            **auth,
            "model_list": _MODEL_LIST[provider]["model_list"],
        }
        for provider, auth in _CUSTOM_PROVIDER_AUTH.items()
    }
    _write_json(CUSTOM_MODEL_FILE, models)


def _load_custom_registry() -> tuple[dict, dict]:
    """从磁盘读取自定义 provider 及其模型。

    Returns:
        (models, auth)：与内置注册表同形状的两份数据（provider -> 配置 / 凭证），
        供 load_model_registry 合并。
    """
    global _CUSTOM_PROVIDER_AUTH

    if not (CUSTOM_MODEL_FILE.exists() and CUSTOM_PROVIDER_FILE.exists()):
        return {}, {}

    _CUSTOM_PROVIDER_AUTH = json.loads(CUSTOM_PROVIDER_FILE.read_text(encoding="utf-8"))
    custom_models = json.loads(CUSTOM_MODEL_FILE.read_text(encoding="utf-8"))
    models = {
        provider: {
            "base_url": entry.get("base_url", ""),
            "model_list": custom_models.get(provider, {}).get("model_list", {}),
        }
        for provider, entry in _CUSTOM_PROVIDER_AUTH.items()
    }
    auth = {
        provider: {"api_key": entry.get("api_key", "")}
        for provider, entry in _CUSTOM_PROVIDER_AUTH.items()
    }
    return models, auth


def load_model_registry() -> None:
    """加载内置 + 自定义注册表；由 core.bootstrap 在启动时调用（测试重调以模拟重启）。"""
    global _MODEL_LIST, _PROVIDER_AUTH

    if not _MODEL_LIST_PATH.exists():
        raise FileNotFoundError(f"{_MODEL_LIST_PATH} does not exist")

    try:
        custom_models, custom_auth = _load_custom_registry()
        _MODEL_LIST = json.loads(_MODEL_LIST_PATH.read_text(encoding="utf-8"))
        _PROVIDER_AUTH = json.loads(PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
        _MODEL_LIST |= custom_models  # 自定义同名 provider 覆盖内置
        _PROVIDER_AUTH |= custom_auth
        _LOGGER.info(f"[Registry Models] providers: {list(_MODEL_LIST)}")
    except Exception as e:
        _LOGGER.error(f"[Loading Model Error] {e}")
        raise


def _update_default_models(provider: str, model: str = "") -> bool:
    """
    当取消配置或注销的提供方，模型出现在默认模型配置中时，清空对应默认模型配置。

    默认模型配置统一为 "provider:model"：模型注销按整串精确匹配，provider 注销按
    "provider:" 前缀匹配。

    Returns:
        返回是否更新了默认模型配置
    """
    updated = False
    for key in ("default_model", "default_sub_model", "default_fallback_model"):
        value = getattr(CONFIGMANAGER.config, key)
        matches = value == f"{provider}:{model}" if model else value.partition(":")[0] == provider
        if value and matches:
            setattr(CONFIGMANAGER.config, key, "")
            updated = True

    if updated:
        try:
            CONFIGMANAGER.save_config()
        except Exception as e:
            _LOGGER.error(f"[Update Default Settings Error] {e}")
            raise
    return updated


def get_provider_list(custom_provider: bool = False) -> list[tuple[str, bool]]:
    """UI provider 列表。

    Args:
        custom_provider: True 只列自定义 provider；False 列内置 provider（含未配置项，
            供 /provider 配置凭证）。

    Returns:
        [(provider, 是否已配置 API key)...]
    """
    if custom_provider:
        return [(provider, provider in _PROVIDER_AUTH) for provider in _CUSTOM_PROVIDER_AUTH]
    return [
        (provider, provider in _PROVIDER_AUTH)
        for provider in _MODEL_LIST
        if provider not in _CUSTOM_PROVIDER_AUTH
    ]


def configure_provider(provider: str, api_key: str) -> None:
    """写入 / 覆盖 provider 的 API key 并落盘。"""
    try:
        _PROVIDER_AUTH[provider] = {"api_key": api_key}
        _write_json(PROVIDER_AUTH_FILE, _PROVIDER_AUTH)
    except Exception as e:
        _LOGGER.exception(f"[Configure Provider Error] {e}")
        raise


def unconfigure_provider(provider: str) -> None:
    """删除某 provider 的 API Key 配置（幂等：未配置 / 已删该 provider 时为空操作）。

    自定义 provider 仅失去凭证，注册信息保留。默认模型配置（provider:model）仍引用该
    provider 时一并清空并复位共享 client，避免默认配置继续指向已删凭证。
    """
    if provider not in _PROVIDER_AUTH:
        return  # 幂等 no-op：未配置 / 已删（UI 对未配置行或重复按 Delete 都安全）
    try:
        _PROVIDER_AUTH.pop(provider)
        _write_json(PROVIDER_AUTH_FILE, _PROVIDER_AUTH)
    except Exception as e:
        _LOGGER.error(f"[Unconfigure Provider Error] {e}")
        raise
    if _update_default_models(provider):
        _refresh_shared_clients()  # 复位内存中持有已删 key 的 client（见 init_client 空默认分支）


def get_model_list(custom_model: bool = False) -> list[tuple[str, str]]:
    """UI 模型列表。

    Args:
        custom_model: True 只列自定义 provider 的模型；False 列全部已配置 provider 的模型。

    Returns:
        [(模型名, provider)...]
    """
    providers = _CUSTOM_PROVIDER_AUTH if custom_model else _PROVIDER_AUTH
    return [
        (model_name, provider)
        for provider in providers
        for model_name in _MODEL_LIST.get(provider, {}).get("model_list", {})
    ]


def login_provider(provider: str, base_url: str, api_key: str = "") -> bool:
    """注册自定义 provider（无需重启即生效）。名称与内置 / 已有 provider 冲突时返回 False。"""
    if provider in _MODEL_LIST:
        _LOGGER.error(f"[Login Provider Error] {provider} exists")
        return False
    try:
        _CUSTOM_PROVIDER_AUTH[provider] = {"base_url": base_url, "api_key": api_key}
        _MODEL_LIST[provider] = {"base_url": base_url, "model_list": {}}
        _write_json(CUSTOM_PROVIDER_FILE, _CUSTOM_PROVIDER_AUTH)
        _save_custom_models()
        _PROVIDER_AUTH[provider] = {"api_key": api_key}
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
        max_output: Optional[int] = None,
) -> bool:
    """在已注册的自定义 provider 下注册模型并落盘。context_length 必须为正（压缩/预算的真源）。"""
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Login Model Error] {provider} is not a custom provider")
        return False
    if context_length <= 0:
        _LOGGER.error(f"[Login Model Error] {model} context_length must be positive")
        return False
    try:
        _MODEL_LIST[provider]["model_list"][model] = {
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
        _save_custom_models()
        _LOGGER.info(f"[Login Model] Login {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Login Model Error] {e}")
        return False
    return True


def logout_provider(provider: str) -> bool:
    """注销自定义 provider（连带其模型与凭证），清理引用它的默认模型配置。"""
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Logout Provider Error] {provider} does not exist")
        return False
    try:
        _CUSTOM_PROVIDER_AUTH.pop(provider)
        _MODEL_LIST.pop(provider)
        _PROVIDER_AUTH.pop(provider, None)  # 从未配置该 provider 时无凭证可删
        _write_json(CUSTOM_PROVIDER_FILE, _CUSTOM_PROVIDER_AUTH)
        _save_custom_models()
        if _update_default_models(provider):
            _refresh_shared_clients()
        _LOGGER.info(f"[Logout Provider] logout {provider} success")
    except Exception as e:
        _LOGGER.error(f"[Logout Provider Error] {e}")
        return False
    return True


def logout_model(provider: str, model: str) -> bool:
    """在自定义 provider 下注销模型，清理引用它的默认模型配置。"""
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Logout Model Error] {provider} does not exist")
        return False
    model_list = _MODEL_LIST[provider]["model_list"]
    if model not in model_list:
        _LOGGER.error(f"[Logout Model Error] {model} does not exist")
        return False
    try:
        model_list.pop(model)
        _save_custom_models()
        if _update_default_models(provider, model):
            _refresh_shared_clients()
        _LOGGER.info(f"[Logout Model] Logout {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Logout Model Error] {e}")
        return False
    return True


# ---------- ModelClient ----------


class ModelClient:

    def __init__(self, sub_model: bool = False) -> None:
        self._sub_model = sub_model
        self.current_provider = ""
        self.current_model = ""
        self.current_model_thinking = True
        self.current_thinking_level = ""
        self.current_client: Optional[OpenAI] = None
        self.current_client_async: Optional[AsyncOpenAI] = None
        self.init_client()

    def _model_config(self) -> dict[str, Any]:
        """当前模型在 _MODEL_LIST 中的注册配置。"""
        return _MODEL_LIST[self.current_provider]["model_list"][self.current_model]

    def _require_selection(self) -> None:
        if not self.current_provider or not self.current_model:
            message = "Please set provider and model"
            _LOGGER.info(message)
            raise RuntimeError(message)

    def load_context_length(self) -> int:
        """加载当前模型注册表中的上下文长度，未选择模型时返回 0。"""
        if not (self.current_provider and self.current_model):
            return 0
        return self._model_config()["context_length"]

    def load_max_output(self) -> Optional[int]:
        """当前模型注册表声明的最大输出；未选择 / 为空（无固定上限）时返回 None。"""
        if not (self.current_provider and self.current_model):
            return None
        return self._model_config().get("max_output") or None

    def compact_threshold(self) -> int:
        """当前模型的上下文压缩触发阈值（随模型切换自动变化）。"""
        return CONFIGMANAGER.config.resolve_compact_threshold(
            self.load_context_length(), self.load_max_output())

    def reserve_threshold(self) -> int:
        """压缩后保留的最近消息 token 预算（按当前上下文长度解析，封顶压缩阈值）。"""
        return CONFIGMANAGER.config.resolve_reserve_threshold(
            self.load_context_length(), self.compact_threshold())

    def clamp_max_tokens(self, requested: int) -> int:
        """把单次调用的 max_tokens 钳制到当前模型固定最大输出；无固定上限时不钳制。"""
        return CONFIGMANAGER.config.clamp_max_tokens(requested, self.load_max_output())

    def init_client(self) -> None:
        """按默认配置（sub client 优先 default_sub_model，未配置则回退默认模型与档位）重建 client。

        "provider:model" 用 partition 解析（模型名可含冒号）。配置为空 / 残缺（从未配置、
        默认提供商刚被 unconfigure_provider 清除）时复位为未选择状态、不抛错：/model 依赖
        此路径修复空默认状态，get_model_client 会给出 "Please set provider and model"。
        """
        config = CONFIGMANAGER.config
        if self._sub_model and config.default_sub_model:
            ref, thinking = config.default_sub_model, config.default_sub_model_thinking_level
        else:
            ref, thinking = config.default_model, config.default_thinking_level
        provider, _sep, model = ref.partition(":")
        if not provider or not model:
            # 复位旧 client（不得继续持有已删凭证）。曾直接 set_model_client("", "") 触发
            # _MODEL_LIST[""] KeyError('')，故此处显式短路。
            self.current_provider = ""
            self.current_model = ""
            self.current_thinking_level = ""
            self.current_model_thinking = False
            self.current_client = None
            self.current_client_async = None
            return
        self.set_model_client(provider, model, thinking)

    def _init_model_client(self) -> None:
        self._require_selection()
        api_key = _PROVIDER_AUTH[self.current_provider]["api_key"]
        base_url = _MODEL_LIST[self.current_provider]["base_url"]
        self.current_client = OpenAI(api_key=api_key, base_url=base_url)
        self.current_client_async = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def set_thinking_level(self, thinking_level: str) -> None:
        """切换思考档位：""（关闭思考）/ map 键（UI 档位）/ 未知键按关闭处理。

        map 值为 false = 该模型不支持此档：thinking 照开、请求时不传 reasoning_effort；
        未知键（如旧版误存的 API 值）不得 KeyError。
        """
        if not thinking_level:
            self.current_thinking_level = ""
            self.current_model_thinking = False
            return
        model_cfg = self._model_config()
        supported = thinking_level in model_cfg["thinking_level_map"]
        self.current_thinking_level = thinking_level if supported else ""
        self.current_model_thinking = model_cfg["thinking"]

    def set_model_client(self, provider: str, model: str, thinking_level: str = "max") -> None:
        """只切换当前会话的内存 client；默认模型 / 提供方 / 思考档位仅 /settings 能改，不落盘。"""
        self.current_provider = provider
        self.current_model = model
        self.set_thinking_level(thinking_level)
        self._init_model_client()

    def _request_kwargs(self) -> dict[str, Any]:
        """thinking 开启才带 extra_body；档位映射值 false 或已关思考时不传 reasoning_effort。"""
        if not self.current_model_thinking:
            return {}
        kwargs: dict[str, Any] = {"extra_body": {"thinking": {"type": "enabled"}}}
        if self.current_thinking_level:
            effort = self._model_config()["thinking_level_map"][self.current_thinking_level]
            if effort:
                kwargs["reasoning_effort"] = effort
        return kwargs

    def get_model_client(self, async_client: bool = False) -> Callable[..., Any]:
        """返回已绑定 model / thinking 参数的 chat.completions.create 调用入口。"""
        self._require_selection()
        request_args = self._request_kwargs()
        _LOGGER.info(f"Model: {self.current_model}({self.current_provider}), "
                     f"request_args: {request_args}, async_client: {async_client}")
        client = self.current_client_async if async_client else self.current_client
        return partial(client.chat.completions.create, model=self.current_model, **request_args)


# ---------- 进程内共享 ModelClient ----------
# 使用方：main agent / 上下文压缩 / teammate → shared_model_client；sub-agent → shared_sub_model_client。
# 配置来源：.harness/.settings.json 的 default_model / default_sub_model 及对应 thinking level。
# 惰性构建：首次实际调用时才实例化，import 阶段无副作用（测试/演示环境可安全导入）。
# 无需加锁：主 agent 与 cron 的 agent 回合已被 TurnRunner 的锁串行，且每轮先建 client 再执行 tool；
# teammate / sub-agent 只能在主回合中途被 spawn，届时对应单例必然已建立，不存在并发首次实例化。

_CLIENT: Optional[ModelClient] = None
_SUB_CLIENT: Optional[ModelClient] = None


def shared_model_client() -> ModelClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ModelClient()
    return _CLIENT


def shared_sub_model_client() -> ModelClient:
    global _SUB_CLIENT
    if _SUB_CLIENT is None:
        _SUB_CLIENT = ModelClient(sub_model=True)
    return _SUB_CLIENT


def _refresh_shared_clients() -> None:
    """默认配置（引用已删 provider/model 时）变更后复位共享 client；子 client 未建立则不提前实例化。"""
    shared_model_client().init_client()
    if _SUB_CLIENT is not None:
        _SUB_CLIENT.init_client()
