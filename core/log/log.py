import logging
from typing import Literal

import colorlog

from core.config import LOG_DIR

if not LOG_DIR.exists():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

# ----- 定义带颜色的格式 -----
# 在 fmt 中直接插入颜色占位符：
#   %(log_color)s 和 %(reset)s 控制 levelname 颜色（由 log_colors 映射）
#   %(white)s / %(blue)s 等固定颜色，后跟 %(reset)s 复位
COLOR_FMT = (
    '%(log_color)s%(levelname)-8s%(reset)s '  # levelname 有颜色，左对齐占8位
    '%(white)s%(asctime)s%(reset)s '  # 时间白色
    '%(blue)s%(pathname)s%(reset)s'  # 路径蓝色
    ':%(lineno)d '  # 行号无颜色（默认）
    '%(white)s%(message)s%(reset)s'  # 消息白色
)

# 颜色格式器（控制台用）
console_formatter = colorlog.ColoredFormatter(
    fmt=COLOR_FMT,
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',  # 红字白底
    }
)

# 普通格式器（文件用，无颜色）
file_formatter = logging.Formatter(
    fmt='%(levelname)-8s  %(asctime)s  %(pathname)s:%(lineno)d  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_logger(module_name: str = ".log", log_type: Literal["file", "console"] = "file") -> logging.Logger:
    module_name = module_name.split(".")[-1]
    file_handler = logging.FileHandler(str(LOG_DIR / f"{module_name}.log"), encoding="utf-8")
    file_handler.setFormatter(file_formatter)
    logger = logging.getLogger(f"{module_name}_log")
    # todo: log level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


if __name__ == "__main__":
    logger = get_logger(__name__, "file")
    logger.debug("调试信息")
    logger.info("普通信息")
    logger.warning("警告信息")
    logger.error("错误信息")
    logger.critical("严重错误")

    try:
        10 / 0
    except Exception as e:
        logger.exception(e)
