"""
Error recovery
"""
import random
import time
from typing import Callable

from src.config import PRIMARY_MODEL, BASE_DELAY_MS, MAX_RETRIES, MAX_CONSECUTIVE, FALLBACK_MODEL
from src.log.log import get_logger

_LOGER = get_logger(__name__)


class RecoveryState:

    def __init__(self):
        super().__init__()
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_1305 = 0
        self.has_attempted_reactive_compact = False
        self.current_model = PRIMARY_MODEL


def retry_delay(attempt: int) -> float:
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.randint(0, int(base * 0.25))


# todo: GLM model only
def with_retry(fn: Callable, state: RecoveryState):
    for attempt in range(MAX_RETRIES):
        try:
            result = fn()
            state.consecutive_529 = 0
            return result
        except Exception as e:
            error_msg = str(e).lower().strip()
            if "429" in error_msg and "1302" in error_msg:
                delay = retry_delay(attempt)
                _LOGER.info(f"[Access rate limit] retry {attempt + 1}/{MAX_RETRIES} "
                            f"after {delay:.1f}s")
                time.sleep(delay)
                continue
            if "429" in error_msg and "1305" in error_msg:
                state.consecutive_1305 += 1
                if state.consecutive_1305 >= MAX_CONSECUTIVE and FALLBACK_MODEL:
                    state.current_model = FALLBACK_MODEL
                    state.consecutive_1305 = 0
                    _LOGER.info(f"[Model overload] switching to {FALLBACK_MODEL}")
                delay = retry_delay(attempt)
                _LOGER.info(f"[Model overload] retry {attempt + 1}/{MAX_RETRIES} "
                            f"after {delay:.1f}s")
                time.sleep(delay)
                continue

            raise

    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


# todo: GLM model only
def is_prompt_too_long_error(e: Exception) -> bool:
    error_message = str(e).lower().strip()
    return "400" in error_message and "1261" in error_message
