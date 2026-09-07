"""
Error recovery
"""
import random
import time
from typing import Callable

from core.config import BASE_DELAY_MS, MAX_RETRIES
from core.log.log import get_logger

_LOGER = get_logger(__name__)


class RecoveryState:

    def __init__(self):
        super().__init__()
        self.has_escalated = False
        self.recovery_count = 0
        # ---- 模型 overload fallback 字段（暂时移除，见 with_retry 的注释块） ----
        # 待 core/model/model.py 扩展出 fallback 模型能力后恢复：
        # self.consecutive_1305 = 0  # 连续 1305 计数，达到 MAX_CONSECUTIVE 后切换 fallback 模型
        # self.current_model = PRIMARY_MODEL  # 旧 env 配置（MODEL），恢复时应改从 ModelClient 读取
        self.has_attempted_reactive_compact = False


def retry_delay(attempt: int) -> float:
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.randint(0, int(base * 0.25))


# todo: GLM model only
# ---- 模型 overload fallback（已注释移除：模型暂时统一为 shared_model_client 的单一配置） ----
# 背景：旧实现里每次请求显式传 model=state.current_model，1305 连续超限后把 current_model 切成
# FALLBACK_MODEL（env）即可换模型重试。统一到 get_model_client() 后，模型在 partial 里已绑定，
# 请求方不再传 model，旧切换方式失效，故删除了 1305 换模型分支（1302/1305 现在都只退避重试同一模型）。
#
# 后续扩展方向（core/model/model.py）：从 .setting.json 的 default_fallback_model（槽位已预留）读取
# fallback 模型，给 ModelClient 增加类似 set_fallback_model()/切换当前模型的能力；恢复点在下方
# with_retry 的 1305 分支——注意切换必须改 shared_model_client() 实例自身的模型后再继续重试
# （fn 内的 get_model_client() 每次都从实例读当前模型，无需恢复 per-request model 参数）：
#
#     if "429" in error_msg and "1305" in error_msg:
#         state.consecutive_1305 += 1
#         if state.consecutive_1305 >= MAX_CONSECUTIVE:
#             # ModelClient 支持后：shared_model_client().switch_to_fallback()（含计数复位）
#             state.consecutive_1305 = 0
#             _LOGER.info("[Model overload] switching to fallback model")
#         delay = retry_delay(attempt)
#         _LOGER.info(f"[Model overload] retry {attempt + 1}/{MAX_RETRIES} "
#                     f"after {delay:.1f}s")
#         time.sleep(delay)
#         continue
#
# 恢复时需同步还原：RecoveryState 的两个字段、MAX_CONSECUTIVE 的 config 导入。
def with_retry(fn: Callable):
    for attempt in range(MAX_RETRIES):
        try:
            result = fn()
            return result
        except Exception as e:
            error_msg = str(e).lower().strip()
            if "429" in error_msg and ("1302" in error_msg or "1305" in error_msg):
                kind = "Access rate limit" if "1302" in error_msg else "Model overload"
                delay = retry_delay(attempt)
                _LOGER.info(f"[{kind}] retry {attempt + 1}/{MAX_RETRIES} "
                            f"after {delay:.1f}s")
                time.sleep(delay)
                continue

            raise

    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


# todo: GLM model only
def is_prompt_too_long_error(e: Exception) -> bool:
    error_message = str(e).lower().strip()
    return "400" in error_message and "1261" in error_message
