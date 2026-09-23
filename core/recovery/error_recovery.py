"""
Error recovery
"""
import asyncio
import random
import time
from enum import Enum
from typing import Callable

from core.config import BASE_DELAY_MS, MAX_RETRIES
from core.log.log import get_logger

_LOGER = get_logger(__name__)


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
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.randint(0, int(base * 0.25))


def get_error_type(error: Exception) -> ErrorType:
    """Return error type based on exception message and current model provider"""
    from core.client import shared_model_client

    error_msg = str(error).lower().strip()
    current_provider = shared_model_client().current_provider

    if current_provider == "deepseek":
        if "40" in error_msg or "422" in error_msg:
            return ErrorType.UnRecoverable
        return ErrorType.RateLimit
    elif current_provider == "qwen":
        if "40" in error_msg:
            return ErrorType.UnRecoverable
        if "429" in error_msg:
            if "bill" in error_msg:
                return ErrorType.UnRecoverable
            return ErrorType.RateLimit
        if "430" in error_msg:
            return ErrorType.UnRecoverable
        # todo: 该错误码绝大多数解决方案为重试
        if "500" in error_msg or "503" in error_msg:
            return ErrorType.RateLimit
    elif current_provider == "z.ai":
        if "429" in error_msg:
            if "1302" in error_msg or "1305" in error_msg:
                return ErrorType.RateLimit
            return ErrorType.UnRecoverable
        if "400" in error_msg:
            if "1261" in error_msg:
                return ErrorType.PromptTooLong
            return ErrorType.UnRecoverable
        if "401" in error_msg or "403" in error_msg or "500" in error_msg:
            return ErrorType.UnRecoverable
    elif current_provider == "kimi":
        if "400" in error_msg or "401" in error_msg:
            return ErrorType.UnRecoverable
        return ErrorType.RateLimit

    return ErrorType.UnRecoverable


def with_retry(fn: Callable):
    for attempt in range(MAX_RETRIES):
        try:
            result = fn()
            return result
        except Exception as e:
            error_type = get_error_type(e)
            if error_type == ErrorType.RateLimit:
                delay = retry_delay(attempt)
                _LOGER.info(f"[Access rate limit] retry {attempt + 1}/{MAX_RETRIES} after {delay:.1f}s")
                time.sleep(delay)
                continue

            raise e

    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


async def with_retry_async(fn: Callable):
    for attempt in range(MAX_RETRIES):
        try:
            result = await fn()
            return result
        except Exception as e:
            error_type = get_error_type(e)
            if error_type == ErrorType.RateLimit:
                delay = retry_delay(attempt)
                _LOGER.info(f"[Access rate limit] retry {attempt + 1}/{MAX_RETRIES} "
                            f"after {delay:.1f}s")
                await asyncio.sleep(delay)
                continue

            raise e

    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    return get_error_type(e) == ErrorType.PromptTooLong
