# -*- coding: utf-8 -*-
"""我想永远陪着你-相片（生图联动）插件。"""

try:  # 正常包内加载
    from .main import AmoImagePlugin, PLUGIN_NAME, PLUGIN_VERSION
except Exception:  # 单测/CI 按文件加载时没有包上下文，退回绝对导入
    from main import AmoImagePlugin, PLUGIN_NAME, PLUGIN_VERSION

__all__ = ["AmoImagePlugin", "PLUGIN_NAME", "PLUGIN_VERSION"]
