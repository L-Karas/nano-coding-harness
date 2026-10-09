"""
Error recovery
"""
import asyncio
import random
from enum import Enum
from typing import Callable

from core.client import shared_model_client
from core.config import CONFIGMANAGER
from core.log.log import get_logger

_LOGGER = get_logger(__name__)


class ErrorType(Enum):
    RateLimit = "rate limit"
    ModelOverload = "model overload"
    PromptTooLong = "prompt too long"
    UnRecoverable = "unrecoverable"


class RecoveryState:

    def __init__(self):
        super().__init__()
        self.has_escalated = False
        self.recovery_count = 0
        # ---- 模型 overload fallback 字段  ----
        # 待 core/model/model.py 扩展出 fallback 模型能力后恢复：


def retry_delay(attempt: int) -> float:
    base = min(CONFIGMANAGER.config.base_retry_delay_ms * (2 ** attempt), 32000) / 1000
    return base + random.randint(0, int(base * 0.25))


def get_error_type(error: Exception, provider: str = "") -> ErrorType:
    """按异常文本与 provider 分类错误；provider 空时取主 client 的当前 provider。"""
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
    return get_error_type(e) == ErrorType.PromptTooLong
