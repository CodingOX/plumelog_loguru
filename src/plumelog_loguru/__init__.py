"""Plumelog-Loguru集成库

提供Loguru与Plumelog系统的集成功能，支持异步Redis日志传输。

主要功能：
- 异步Redis日志传输
- 批量处理优化
- 完整的类型提示
- 线程安全操作
- 连接池管理
- 智能重试机制

使用示例：
    from loguru import logger
    from plumelog_loguru import create_redis_sink, PlumelogSettings

    # 使用默认配置
    logger.add(create_redis_sink())

    # 使用自定义配置
    config = PlumelogSettings(
        app_name="my_app",
        env="production",
        redis_host="localhost",
        redis_port=6379
    )
    logger.add(create_redis_sink(config))
"""

from .config import PlumelogSettings
from .extractor import FieldExtractor
from .models import LogRecord, CallerInfo, SystemInfo, RedisConnectionInfo, BatchConfig
from .redis_client import AsyncRedisClient
from .redis_sink import RedisSink, create_redis_sink

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

__all__ = [
    # 主要接口
    "create_redis_sink",
    "RedisSink",
    # 配置类
    "PlumelogSettings",
    # 数据模型
    "LogRecord",
    "CallerInfo",
    "SystemInfo",
    "RedisConnectionInfo",
    "BatchConfig",
    # 核心组件
    "AsyncRedisClient",
    "FieldExtractor",
    # 元数据
    "__version__",
    "__author__",
    "__email__",
]
