"""测试异步 Redis 客户端模块"""

import pytest
from _pytest.monkeypatch import MonkeyPatch
from redis.exceptions import TimeoutError

from plumelog_loguru import PlumelogSettings
from plumelog_loguru.redis_client import AsyncRedisClient


@pytest.mark.asyncio
async def test_handle_send_error_cleans_up_timeout_error(
    test_config: PlumelogSettings, monkeypatch: MonkeyPatch
) -> None:
    """命令超时也应视为连接不健康并清理连接状态"""
    client = AsyncRedisClient(test_config)
    client._connected = True
    cleanup_called = False

    async def fake_cleanup() -> None:
        nonlocal cleanup_called
        cleanup_called = True
        client.redis = None
        client.pool = None
        client._connected = False

    monkeypatch.setattr(client, "_cleanup_on_error", fake_cleanup)

    await client._handle_send_error(TimeoutError("timeout"), attempt=2, log_count=1)

    assert cleanup_called is True
    assert client._connected is False
    assert client.redis is None
    assert client.pool is None
