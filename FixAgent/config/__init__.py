"""
配置模块

项目全局配置，通过环境变量 + .env 文件加载。
"""

from .settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
