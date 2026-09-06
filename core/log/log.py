import logging

from core.config import LOG_DIR

if not LOG_DIR.exists():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

# 普通格式器（文件用，无颜色）
file_formatter = logging.Formatter(
    fmt='%(levelname)-8s  %(asctime)s  %(pathname)s:%(lineno)d  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_logger(module_name: str = ".log") -> logging.Logger:
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
    logger = get_logger(__name__)
    logger.debug("调试信息")
    logger.info("普通信息")
    logger.warning("警告信息")
    logger.error("错误信息")
    logger.critical("严重错误")

    try:
        10 / 0
    except Exception as e:
        logger.exception(e)
