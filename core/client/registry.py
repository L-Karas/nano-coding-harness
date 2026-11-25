"""模型注册表：内置 / 自定义 provider 与模型的加载、凭证和持久化。

运行时以 ``_MODEL_LIST``（内置 + 自定义合并，模型真源）与 ``_PROVIDER_AUTH``（已配置 key 的
provider）为准；``_CUSTOM_PROVIDER_AUTH`` 只用于区分自定义 / 内置 provider，两个自定义 JSON
均由它派生，不另存一份运行时副本。

注册表本身不持有 ModelClient；凭证 / 模型变化后通过局部导入调用 ``model_client`` 的失效与
复位入口，避免 registry ↔ model_client 的模块级循环依赖。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import CONFIGMANAGER, PATHS
from core.shared.logger import get_logger

_MODEL_LIST_PATH: Path = Path(__file__).parent / "models.json"

_MODEL_LIST: dict[str, dict] = {}  # provider -> {"base_url", "model_list": {model: cfg}}
_PROVIDER_AUTH: dict[str, dict] = {}  # provider -> {"api_key"}；条目存在时 key 仍可能为空
_CUSTOM_PROVIDER_AUTH: dict[str, dict] = {}  # provider -> {"base_url", "api_key"}，全部自定义 provider

_LOGGER = get_logger(__name__)


# ---------- client 失效入口（实现位于 model_client） ----------


def _clear_explicit_client_cache() -> None:
    """清空显式子代理 client 缓存；实际实现见 model_client（局部导入断开循环依赖）。"""
    from core.client.model_client import _clear_explicit_client_cache as clear
    clear()


def _refresh_shared_clients() -> None:
    """清显式缓存并复位共享 client；实际实现见 model_client（子 client 未建立则不提前实例化）。"""
    from core.client.model_client import _refresh_shared_clients as refresh
    refresh()


# ---------- 注册表：加载 / 持久化 / 自定义 provider·model 管理 ----------


def _write_json(path: Path, data: dict) -> None:
    """统一 JSON 落盘格式（UTF-8 / 中文不转义 / 4 空格缩进）；异常由调用方处理。

    Args:
        path: 目标文件路径。
        data: 待序列化并写入的 JSON 数据。
    """
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
    _write_json(PATHS.CUSTOM_MODEL_FILE, models)


def _load_custom_registry() -> tuple[dict, dict]:
    """从磁盘读取自定义 provider 及其模型。

    Returns:
        (models, auth)：与内置注册表同形状的两份数据（provider -> 配置 / 凭证），
        供 load_model_registry 合并。
    """
    global _CUSTOM_PROVIDER_AUTH

    if not (PATHS.CUSTOM_MODEL_FILE.exists() and PATHS.CUSTOM_PROVIDER_FILE.exists()):
        return {}, {}

    _CUSTOM_PROVIDER_AUTH = json.loads(PATHS.CUSTOM_PROVIDER_FILE.read_text(encoding="utf-8"))
    custom_models = json.loads(PATHS.CUSTOM_MODEL_FILE.read_text(encoding="utf-8"))
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
    """加载内置 + 自定义注册表；由 core.bootstrap 在启动时调用（测试重调以模拟重启）。

    Raises:
        FileNotFoundError: 内置 models.json 不存在。
    """
    global _MODEL_LIST, _PROVIDER_AUTH

    if not _MODEL_LIST_PATH.exists():
        raise FileNotFoundError(f"{_MODEL_LIST_PATH} does not exist")

    try:
        _clear_explicit_client_cache()  # 注册表换了，按旧配置缓存的 client 不得继续复用
        custom_models, custom_auth = _load_custom_registry()
        _MODEL_LIST = json.loads(_MODEL_LIST_PATH.read_text(encoding="utf-8"))
        _PROVIDER_AUTH = json.loads(PATHS.PROVIDER_AUTH_FILE.read_text(encoding="utf-8"))
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

    Args:
        provider: 提供方名称。
        model: 要注销的模型名；省略时按 provider 注销处理。

    Returns:
        返回是否更新了默认模型配置。
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


def is_provider_available(provider: str) -> bool:
    """provider 是否配置了可用的 API key（空 key 视同未配置）。

    Args:
        provider: 提供方名称。

    Returns:
        已登记且 API key 非空时为 True。
    """
    return bool(_PROVIDER_AUTH.get(provider, {}).get("api_key"))


def get_provider_list(custom_provider: bool = False) -> list[tuple[str, bool]]:
    """UI provider 列表。

    Args:
        custom_provider: True 只列自定义 provider；False 列内置 provider（含未配置项，
            供 /provider 配置凭证）。

    Returns:
        [(provider, 是否已配置 API key)...]
    """
    if custom_provider:
        return [(provider, is_provider_available(provider)) for provider in _CUSTOM_PROVIDER_AUTH]
    return [
        (provider, is_provider_available(provider))
        for provider in _MODEL_LIST
        if provider not in _CUSTOM_PROVIDER_AUTH
    ]


def configure_provider(provider: str, api_key: str) -> None:
    """写入 / 覆盖 provider 的 API key 并落盘。

    Args:
        provider: 提供方名称。
        api_key: 要保存的 API key。
    """
    try:
        _PROVIDER_AUTH[provider] = {"api_key": api_key}
        _write_json(PATHS.PROVIDER_AUTH_FILE, _PROVIDER_AUTH)
        _clear_explicit_client_cache()  # 显式子代理 client 必须拿到新 key
    except Exception as e:
        _LOGGER.exception(f"[Configure Provider Error] {e}")
        raise


def unconfigure_provider(provider: str) -> None:
    """删除某 provider 的 API Key 配置（幂等：未配置 / 已删该 provider 时为空操作）。

    自定义 provider 仅失去凭证，注册信息保留。默认模型配置（provider:model）仍引用该
    provider 时一并清空并复位共享 client，避免默认配置继续指向已删凭证。

    Args:
        provider: 要移除凭证的提供方名称。
    """
    if provider not in _PROVIDER_AUTH:
        return  # 幂等 no-op：未配置 / 已删（UI 对未配置行或重复按 Delete 都安全）
    try:
        _PROVIDER_AUTH.pop(provider)
        _write_json(PATHS.PROVIDER_AUTH_FILE, _PROVIDER_AUTH)
    except Exception as e:
        _LOGGER.error(f"[Unconfigure Provider Error] {e}")
        raise
    if _update_default_models(provider):
        _refresh_shared_clients()  # 复位内存中持有已删 key 的 client（见 init_client 空默认分支）
    else:
        _clear_explicit_client_cache()


def get_model_list(custom_model: bool = False) -> list[tuple[str, str]]:
    """UI 模型列表。

    Args:
        custom_model: True 只列自定义 provider 的模型（含未配置 key 的项，供注销）；
            False 只列已配置 API key 的 provider 的模型。

    Returns:
        [(模型名, provider)...]
    """
    providers = _CUSTOM_PROVIDER_AUTH if custom_model else _PROVIDER_AUTH
    return [
        (model_name, provider)
        for provider in providers
        if custom_model or is_provider_available(provider)
        for model_name in _MODEL_LIST.get(provider, {}).get("model_list", {})
    ]


@dataclass(frozen=True)
class AvailableModel:
    """list_models 展示的一行：已配置 provider 下一个可用模型的能力快照。"""
    provider: str
    model: str
    vision: bool
    thinking: bool
    thinking_levels: tuple[str, ...]
    context_length: int
    max_output: Optional[int]

    @property
    def ref(self) -> str:
        """模型的全限定引用。

        Returns:
            ``provider:model``。
        """
        return f"{self.provider}:{self.model}"


def list_available_models() -> list[AvailableModel]:
    """列出已配置 API key 的 provider 下全部可用模型（按 provider / 模型名排序）。

    Returns:
        模型能力快照列表；没有已配置 provider 时为空。
    """
    available = []
    for provider in _PROVIDER_AUTH:
        if not is_provider_available(provider):
            continue  # 空 key 与 validate_model_choice 一致：不可用
        for model_name, model_cfg in _MODEL_LIST.get(provider, {}).get("model_list", {}).items():
            level_map = model_cfg.get("thinking_level_map", {})
            available.append(AvailableModel(
                provider=provider,
                model=model_name,
                vision=bool(model_cfg.get("vision", False)),
                thinking=bool(model_cfg.get("thinking", False)),
                # 只列会真正发送 reasoning_effort 的档位（map 值为 false = 该档降级不发送）
                thinking_levels=tuple(level for level, effort in level_map.items() if effort),
                context_length=int(model_cfg.get("context_length") or 0),
                max_output=model_cfg.get("max_output"),
            ))
    return sorted(available, key=lambda item: (item.provider, item.model))


def login_provider(provider: str, base_url: str, api_key: str = "") -> bool:
    """注册自定义 provider（无需重启即生效）。名称与内置 / 已有 provider 冲突时返回 False。

    Args:
        provider: 自定义提供方名称。
        base_url: OpenAI 兼容接口的 base URL。
        api_key: 初始 API key；可为空，之后再用 configure_provider 写入。

    Returns:
        是否注册成功。
    """
    if provider in _MODEL_LIST:
        _LOGGER.error(f"[Login Provider Error] {provider} exists")
        return False
    try:
        _CUSTOM_PROVIDER_AUTH[provider] = {"base_url": base_url, "api_key": api_key}
        _MODEL_LIST[provider] = {"base_url": base_url, "model_list": {}}
        _write_json(PATHS.CUSTOM_PROVIDER_FILE, _CUSTOM_PROVIDER_AUTH)
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
    """在已注册的自定义 provider 下注册模型并落盘。context_length 必须为正（压缩/预算的真源）。

    Args:
        provider: 已注册的自定义提供方名称。
        model: 模型名称。
        thinking: 是否支持思考 / reasoning。
        vision: 是否支持图像输入。
        context_length: 上下文窗口长度，必须为正数。
        max_output: 最大输出 token 数；None 表示无固定上限。

    Returns:
        是否注册成功。
    """
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
        _clear_explicit_client_cache()
        _LOGGER.info(f"[Login Model] Login {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Login Model Error] {e}")
        return False
    return True


def logout_provider(provider: str) -> bool:
    """注销自定义 provider（连带其模型与凭证），清理引用它的默认模型配置。

    Args:
        provider: 要注销的自定义提供方名称。

    Returns:
        是否注销成功。
    """
    if provider not in _CUSTOM_PROVIDER_AUTH:
        _LOGGER.error(f"[Logout Provider Error] {provider} does not exist")
        return False
    try:
        _CUSTOM_PROVIDER_AUTH.pop(provider)
        _MODEL_LIST.pop(provider)
        _PROVIDER_AUTH.pop(provider, None)  # 从未配置该 provider 时无凭证可删
        _write_json(PATHS.CUSTOM_PROVIDER_FILE, _CUSTOM_PROVIDER_AUTH)
        _save_custom_models()
        if _update_default_models(provider):
            _refresh_shared_clients()
        else:
            _clear_explicit_client_cache()
        _LOGGER.info(f"[Logout Provider] logout {provider} success")
    except Exception as e:
        _LOGGER.error(f"[Logout Provider Error] {e}")
        return False
    return True


def logout_model(provider: str, model: str) -> bool:
    """在自定义 provider 下注销模型，清理引用它的默认模型配置。

    Args:
        provider: 模型所属的自定义提供方名称。
        model: 要注销的模型名称。

    Returns:
        是否注销成功。
    """
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
        else:
            _clear_explicit_client_cache()
        _LOGGER.info(f"[Logout Model] Logout {model}({provider}) success")
    except Exception as e:
        _LOGGER.error(f"[Logout Model Error] {e}")
        return False
    return True
