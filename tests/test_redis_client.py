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


@pytest.mark.asyncio
async def test_send_log_records_uses_single_lpush_command(
    test_config: PlumelogSettings, monkeypatch: MonkeyPatch
) -> None:
    """批量发送应合并为单条 lpush 命令，而非逐条调用

    原实现用 pipeline 逐条 lpush（N+1 问题），Redis 侧仍需解析 N 条命令。
    修复后应使用 lpush(key, *values) 合并为 1 条命令。
    """
    from plumelog_loguru.models import LogRecord

    def _make_record(content: str) -> LogRecord:
        return LogRecord(
            server_name="s", app_name="a", env="test",
            method="m", content=content, log_level="INFO",
            class_name="C", thread_name="T",
            seq=1, date_time="2024-01-01 00:00:00.000", dt_time=0,
        )

    client = AsyncRedisClient(test_config)
    client._connected = True

    lpush_calls: list[tuple] = []

    class FakeRedis:
        async def lpush(self, key: str, *values: str) -> int:
            lpush_calls.append((key, values))
            return len(values)

    client.redis = FakeRedis()  # type: ignore[assignment]

    records = [_make_record(f"msg-{i}") for i in range(5)]
    await client.send_log_records(records)

    # 必须只调用 1 次 lpush，并且所有 5 条 JSON 都在同一次调用中
    assert len(lpush_calls) == 1, f"期望 1 次 lpush，实际 {len(lpush_calls)} 次"
    assert len(lpush_calls[0][1]) == 5, "期望 5 个值在同一次 lpush 中"
