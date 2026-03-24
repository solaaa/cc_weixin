"""日志配置模块。输出到 LOG/ 目录，区分 info 和 error。"""

import logging
import os
import time

_initialized = False


def setup_logger(log_dir=None):
    """
    配置全局日志。

    - LOG/info.log：所有级别（INFO 及以上）
    - LOG/error.log：仅 WARNING 及以上
    - 终端：同步输出
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if log_dir is None:
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "LOG",
        )
    os.makedirs(log_dir, exist_ok=True)

    # 根 logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # INFO 文件 handler
    info_handler = logging.FileHandler(
        os.path.join(log_dir, "info.log"),
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(fmt)

    # ERROR 文件 handler
    error_handler = logging.FileHandler(
        os.path.join(log_dir, "error.log"),
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)

    # 终端 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    root.addHandler(info_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)


def get_logger(name):
    """获取指定名称的 logger。"""
    return logging.getLogger(name)
