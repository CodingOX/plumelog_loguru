"""基础测试配置和工具函数"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from plumelog_loguru import PlumelogSettings


@pytest.fixture
def test_config() -> PlumelogSettings:
    """测试用的配置对象"""
    return PlumelogSettings(
        app_name="test_app",
        env="test",
        redis_host="localhost",
        redis_port=6379,
        redis_db=1,  # 使用测试数据库
        batch_size=10,
        batch_interval_seconds=0.1,
        queue_max_size=100,
    )


@pytest.fixture
def mock_redis():
    """模拟Redis客户端"""
    mock = AsyncMock()
    mock.ping.return_value = True
    mock.lpush.return_value = 1
    mock.pipeline.return_value.__aenter__.return_value.execute.return_value = [1, 1, 1]
    return mock


@pytest.fixture
def event_loop():
    """提供事件循环用于异步测试"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
