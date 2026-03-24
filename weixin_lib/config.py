"""配置管理模块。加载并合并默认配置与用户自定义配置。"""

import json
import os

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


def load_config(user_config_path=None):
    """
    加载配置。优先使用用户指定的配置文件，缺失字段用默认值补齐。

    查找顺序:
        1. user_config_path（如果传入）
        2. 项目根目录 weixin_config.json
        3. 仅使用默认配置
    """
    # 加载默认配置
    with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 查找用户配置
    if user_config_path is None:
        root_config = os.path.join(os.getcwd(), "weixin_config.json")
        if os.path.exists(root_config):
            user_config_path = root_config

    # 合并用户配置
    if user_config_path and os.path.exists(user_config_path):
        with open(user_config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        _deep_merge(config, user_config)

    return config


def _deep_merge(base, override):
    """将 override 深度合并到 base（就地修改）。"""
    for key, value in override.items():
        if key.startswith("_"):
            continue
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def should_forward(config, event_type):
    """判断某种事件类型是否应该转发到微信。"""
    if event_type == "result":
        return True
    return config.get("forward_events", {}).get(event_type, False)


def get_prefix(config, event_type):
    """获取事件类型的消息前缀。"""
    return config.get("message_prefix", {}).get(event_type, "")


def get_max_length(config):
    """获取单条消息最大长度。"""
    return config.get("max_message_length", 2000)
