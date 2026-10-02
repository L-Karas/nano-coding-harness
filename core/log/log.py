import logging

from core.config import LOG_DIR

# 普通格式器（文件用，无颜色）
file_formatter = logging.Formatter(
    fmt='%(levelname)-8s  %(asctime)s  %(pathname)s:%(lineno)d  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_logger(module_name: str = ".log") -> logging.Logger:
    """按模块名返回 logger；同名调用幂等（不会重复挂 handler、重复写日志）。"""
    name = module_name.split(".")[-1]
    logger = logging.getLogger(f"{name}_log")
    if not logger.handlers:
        file_handler = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger
