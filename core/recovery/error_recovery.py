"""
Error recovery
"""
import asyncio
import random
from enum import Enum
from typing import Callable

from core.client import shared_model_client
from core.config import CONFIGMANAGER
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)


class ErrorType(Enum):
    RateLimit = "rate limit"
    ModelOverload = "model overload"
    PromptTooLong = "prompt too long"
    UnRecoverable = "unrecoverable"


class RecoveryState:

    def __init__(self) -> None:
        """初始化恢复状态计数。"""
        super().__init__()
        self.has_escalated = False
        self.recovery_count = 0
        # ---- 模型 overload fallback 字段  ----
        # 待 core/model/model.py 扩展出 fallback 模型能力后恢复：


def retry_delay(attempt: int) -> float:
    """计算第 attempt 次重试前的退避时间（指数增长 + 抖动）。

    Args:
        attempt: 重试序号（0 起始）。

    Returns:
        退避秒数，封顶 32 秒再加最多 25% 抖动。
    """
    base = min(CONFIGMANAGER.config.base_retry_delay_ms * (2 ** attempt), 32000) / 1000
    return base + random.randint(0, int(base * 0.25))


def get_error_type(error: Exception, provider: str = "") -> ErrorType:
    """按异常文本与 provider 分类错误；provider 空时取主 client 的当前 provider。

    Args:
        error: 捕获到的异常。
        provider: 提供方名称；空字符串表示自动取当前主模型。

    Returns:
        错误类型。
    """
    error_msg = str(error).lower().strip()
    current_provider = provider or shared_model_client().current_provider

    if current_provider == "Deepseek":
        if "40" in error_msg or "422" in error_msg:
            return ErrorType.UnRecoverable
        return ErrorType.RateLimit
    elif current_provider == "Qwen":
        if "40" in error_msg:
            return ErrorType.UnRecoverable
        if "429" in error_msg:
            if "bill" in error_msg:
                return ErrorType.UnRecoverable
            return ErrorType.RateLimit
        if "430" in error_msg:
            return ErrorType.UnRecoverable
        # 该错误码绝大多数解决方案为重试
        if "500" in error_msg or "503" in error_msg:
            return ErrorType.RateLimit
    elif current_provider == "Z.AI":
        if "429" in error_msg:
            if "1302" in error_msg or "1305" in error_msg:
                return ErrorType.RateLimit
            return ErrorType.UnRecoverable
        if "400" in error_msg:
            if "1261" in error_msg:
                return ErrorType.PromptTooLong
            return ErrorType.UnRecoverable
        if any(code in error_msg for code in ("401", "403", "500")):
            return ErrorType.UnRecoverable
    elif current_provider == "Kimi":
        if "400" in error_msg or "401" in error_msg:
            return ErrorType.UnRecoverable
        return ErrorType.RateLimit
    elif current_provider in ("Xiaomi MiMo", "Xiaomi MiMo Token Plan"):
        if any(code in error_msg for code in ("400", "401", "402", "403", "404", "421")):
            return ErrorType.UnRecoverable
        if any(code in error_msg for code in ("429", "500", "503")):
            return ErrorType.RateLimit

    # MiniMax / Tencent 暂无分类规则：与未知 provider 一样按不可恢复处理
    return ErrorType.UnRecoverable


async def with_retry_async(fn: Callable, provider: str = ""):
    """在可恢复错误上按指数退避重试；不可恢复错误立即上抛。

    Args:
        fn: 返回 awaitable 的调用。
        provider: 提供方名称；空字符串表示自动取当前主模型。

    Returns:
        fn 的成功返回值。

    Raises:
        Exception: 不可恢复错误 / PromptTooLong 原样上抛。
        RuntimeError: 达到最大重试次数仍失败。
    """
    max_retries = CONFIGMANAGER.config.max_retries
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as e:
            _LOGGER.exception(f"[Error Recovery] Call model error")
            error_type = get_error_type(e, provider)
            if error_type in (ErrorType.UnRecoverable, ErrorType.PromptTooLong):
                raise
            delay = retry_delay(attempt)
            _LOGGER.info(
                f"[Access rate limit] retry {attempt + 1}/{max_retries} after {delay:.1f}s")
            await asyncio.sleep(delay)

    raise RuntimeError(f"Max retries ({max_retries}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    """判断异常是否为上下文超长错误。

    Args:
        e: 捕获到的异常。

    Returns:
        分类为 PromptTooLong 时为 True。
    """
    return get_error_type(e) == ErrorType.PromptTooLong
