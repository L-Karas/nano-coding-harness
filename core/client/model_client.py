"""运行时 ModelClient：按 provider / 模型 / 思考档位构造 OpenAI 兼容请求。

进程内共享实例见 ``shared_model_client`` / ``shared_sub_model_client``；单次子代理的显式选择
由 ``model_client_for`` 按 (provider, model, thinking_level) 缓存，与共享实例分离。

注册表状态由 ``core.client.registry`` 持有；本模块在调用时读取其模块属性，注册表重载后无需
重新绑定。
"""

from collections.abc import Callable
from functools import partial
from typing import Any, Optional

from openai import AsyncOpenAI

from core.client import registry
from core.client.model_choice import ModelChoice, validate_model_choice
from core.config import CONFIGMANAGER
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)


class ModelClient:

    def __init__(self, sub_model: bool = False, choice: Optional[ModelChoice] = None) -> None:
        """初始化客户端；缺省按 settings 默认配置建立连接，或按显式选择直接绑定模型。

        Args:
            sub_model: True 优先 default_sub_model（未配置时回退 default_model 与其档位）；
                False 使用 default_model。给出 choice 时 sub_model 不生效（不读 settings）。
            choice: 显式子代理模型选择；给出时先校验，再只按该模型配置建 client。
        """
        self._sub_model = sub_model
        self.current_provider = ""
        self.current_model = ""
        self.current_model_thinking = True
        self.current_thinking_level = ""
        self.current_client_async: Optional[AsyncOpenAI] = None
        if choice is not None:
            validate_model_choice(choice)
            self.set_model_client(choice.provider, choice.model_name, choice.thinking_level)
        else:
            self.init_client()

    def _model_config(self) -> dict[str, Any]:
        """当前模型在注册表中的配置。

        Returns:
            模型注册配置字典。
        """
        return registry._MODEL_LIST[self.current_provider]["model_list"][self.current_model]

    def _require_selection(self) -> None:
        """校验已选择 provider 与 model。

        Raises:
            RuntimeError: 尚未设置 provider 或 model。
        """
        if not self.current_provider or not self.current_model:
            message = "Please set provider and model"
            _LOGGER.info(message)
            raise RuntimeError(message)

    def load_context_length(self) -> int:
        """加载当前模型注册表中的上下文长度，未选择模型时返回 0。

        Returns:
            上下文窗口长度；未选择模型时为 0。
        """
        if not (self.current_provider and self.current_model):
            return 0
        return self._model_config()["context_length"]

    def load_max_output(self) -> Optional[int]:
        """当前模型注册表声明的最大输出；未选择 / 为空（无固定上限）时返回 None。

        Returns:
            最大输出 token 数，或 None。
        """
        if not (self.current_provider and self.current_model):
            return None
        return self._model_config().get("max_output") or None

    def supports_vision(self) -> bool:
        """当前模型是否声明支持图像输入；未选择模型时为 False。

        Returns:
            支持图像输入时为 True。
        """
        if not (self.current_provider and self.current_model):
            return False
        return bool(self._model_config().get("vision", False))

    def compact_trigger_tokens(self) -> int:
        """当前模型的上下文压缩触发阈值（优先配置值，最终不超过窗口 80%）。

        Returns:
            压缩触发阈值；无可用上下文窗口时为 0。
        """
        return CONFIGMANAGER.config.resolve_compact_trigger_tokens(self.load_context_length())

    def compact_reserve_tokens(self) -> int:
        """压缩后保留的最近消息 token 预算（绝对值，封顶压缩阈值）。

        Returns:
            保留预算 token 数。
        """
        return CONFIGMANAGER.config.resolve_compact_reserve_tokens(self.compact_trigger_tokens())

    def clamp_max_tokens(self, requested: int) -> int:
        """把单次调用的 max_tokens 钳制到当前模型固定最大输出；无固定上限时不钳制。

        Args:
            requested: 期望的 max_tokens。

        Returns:
            钳制后的 max_tokens。
        """
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
            self.current_client_async = None
            return
        self.set_model_client(provider, model, thinking)

    def _init_model_client(self) -> None:
        """用当前 provider 的凭证与 base_url 重建异步 OpenAI client。

        Raises:
            RuntimeError: 尚未选择 provider 或 model。
            KeyError: 当前 provider 没有可用的注册配置或 API key。
        """
        self._require_selection()
        api_key = registry._PROVIDER_AUTH[self.current_provider]["api_key"]
        base_url = registry._MODEL_LIST[self.current_provider]["base_url"]
        self.current_client_async = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def set_thinking_level(self, thinking_level: str) -> None:
        """切换思考档位：""（关闭思考）/ map 键（UI 档位）/ 未知键按关闭处理。

        map 值为 false = 该模型不支持此档：thinking 照开、请求时不传 reasoning_effort；
        未知键（如旧版误存的 API 值）不得 KeyError。

        Args:
            thinking_level: 空字符串关闭思考；否则为 thinking_level_map 中的档位键。
        """
        if not thinking_level:
            self.current_thinking_level = ""
            self.current_model_thinking = False
            return
        model_cfg = self._model_config()
        # todo: supported 必为 true，逻辑多余？
        supported = thinking_level in model_cfg["thinking_level_map"]
        self.current_thinking_level = thinking_level if supported else ""
        self.current_model_thinking = model_cfg["thinking"]

    def set_model_client(
        self,
        provider: str,
        model: str,
        thinking_level: str = "max",
    ) -> None:
        """只切换当前会话的内存 client；默认模型 / 提供方 / 思考档位仅 /settings 能改，不落盘。

        Args:
            provider: 提供方名称。
            model: 模型名称。
            thinking_level: 初始思考档位，缺省为 "max"。
        """
        self.current_provider = provider
        self.current_model = model
        self.set_thinking_level(thinking_level)
        self._init_model_client()

    def _request_kwargs(self) -> dict[str, Any]:
        """thinking 开启才带 extra_body；档位映射值 false 或已关思考时不传 reasoning_effort。

        Returns:
            追加到请求中的参数 dict。
        """
        if not self.current_model_thinking:
            return {}
        kwargs: dict[str, Any] = {"extra_body": {"thinking": {"type": "enabled"}}}
        # todo: set_thinking_level 不受该方法的 set_thinking_level 必不空影响
        if self.current_thinking_level:
            effort = self._model_config()["thinking_level_map"][self.current_thinking_level]
            if effort:
                kwargs["reasoning_effort"] = effort
        return kwargs

    def get_model_client(self) -> Callable[..., Any]:
        """返回已绑定 model / thinking 参数的异步 chat.completions.create 调用入口。

        Returns:
            可直接传入 messages 等参数的异步调用入口。
        """
        self._require_selection()
        request_args = self._request_kwargs()
        _LOGGER.info(
            f"Model: {self.current_model}({self.current_provider}), "
            f"request_args: {request_args}"
        )
        return partial(
            self.current_client_async.chat.completions.create,
            model=self.current_model, **request_args
        )


def model_supports_vision(client: Any) -> bool:
    """client 的当前模型能否接收图片；未声明该能力的测试替身按支持处理。

    Args:
        client: 模型客户端；可提供 supports_vision 方法或属性。

    Returns:
        是否支持图像输入。
    """
    supports = getattr(client, "supports_vision", None)
    if callable(supports):
        return bool(supports())
    return bool(supports) if supports is not None else True


# ---------- 进程内共享 ModelClient ----------
# 使用方：main agent / 上下文压缩 → shared_model_client；缺省子代理 → shared_sub_model_client；
# 显式 model_choice 的子代理 → model_client_for（独立缓存，不复用共享单例）。
# 配置来源：.nano-harness/.settings.json 的 default_model / default_sub_model 及对应 thinking level。
# 惰性构建：首次实际调用时才实例化，import 阶段无副作用（测试/演示环境可安全导入）。
# 无需加锁：主 agent 与 cron 的 agent 回合已被 TurnRunner 的锁串行，且每轮先建 client 再执行 tool；
# sub-agent 只能在主回合中途被 spawn，届时对应单例必然已建立，不存在并发首次实例化。

_CLIENT: Optional[ModelClient] = None
_SUB_CLIENT: Optional[ModelClient] = None
# 显式子代理模型 → client：键含 thinking_level（档位绑定在 client 上，同模型不同档位互不覆盖）。
_MODEL_CLIENT_CACHE: dict[tuple[str, str, str], ModelClient] = {}


def shared_model_client() -> ModelClient:
    """返回进程内共享的主模型客户端（首次调用时惰性创建）。

    Returns:
        使用 default_model 配置的 ModelClient 实例。
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ModelClient()
    return _CLIENT


def shared_sub_model_client() -> ModelClient:
    """返回进程内共享的子 agent 模型客户端（首次调用时惰性创建）。

    Returns:
        使用 default_sub_model（未配置时回退 default_model）配置的 ModelClient 实例。
    """
    global _SUB_CLIENT
    if _SUB_CLIENT is None:
        _SUB_CLIENT = ModelClient(sub_model=True)
    return _SUB_CLIENT


def model_client_for(choice: ModelChoice) -> ModelClient:
    """按显式模型选择取 client；同一 (provider, model, thinking_level) 复用实例。

    与共享单例分离：多个子代理并发使用不同模型时互不覆盖。

    Args:
        choice: 子代理的模型选择。

    Returns:
        绑定该模型与档位的 ModelClient。
    """
    key = choice.cache_key()
    client = _MODEL_CLIENT_CACHE.get(key)
    if client is None:
        client = ModelClient(choice=choice)
        _MODEL_CLIENT_CACHE[key] = client
    return client


# todo: 代理执行时, 是否允许对模型进行配置
def _clear_explicit_client_cache() -> None:
    """清空显式子代理 client 缓存；供注册表 / 凭证变更时调用。"""
    _MODEL_CLIENT_CACHE.clear()


def _refresh_shared_clients() -> None:
    """默认配置（引用已删 provider/model 时）变更后复位共享 client 与显式模型缓存；
    子 client 未建立则不提前实例化。"""
    _clear_explicit_client_cache()
    shared_model_client().init_client()
    if _SUB_CLIENT is not None:
        _SUB_CLIENT.init_client()
