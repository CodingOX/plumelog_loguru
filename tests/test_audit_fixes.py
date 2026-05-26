"""审计修复验证测试

覆盖以下修复项：
1. deque 冗余代码简化（不再手动 popleft）
2. print() 限频告警
3. json.dumps 移到 retry 循环外
4. 异常消息脱敏
5. disconnect() 资源清理加固
"""

from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from _pytest.monkeypatch import MonkeyPatch

from plumelog_loguru import PlumelogSettings
from plumelog_loguru.models import LogRecord
from plumelog_loguru.redis_client import AsyncRedisClient
from plumelog_loguru.redis_sink import RedisSink


def _make_record(content: str = "test", seq: int = 1) -> LogRecord:
    return LogRecord(
        server_name="s",
        app_name="a",
        env="test",
        method="m",
        content=content,
        log_level="INFO",
        class_name="C",
        thread_name="T",
        seq=seq,
        date_time="2024-01-01 00:00:00.000",
        dt_time=0,
    )


# =============================================================================
# Fix 1: deque 冗余代码简化
# =============================================================================


class TestDequeSimplification:
    """验证 _store_to_temp_buffer 简化后 deque(maxlen=N) 的溢出行为"""

    def test_deque_overflow_drops_exactly_one(self) -> None:
        """缓存满时新增 1 条应恰好丢弃 1 条（最旧的），不会双重丢弃"""
        config = PlumelogSettings(app_name="test", env="test", temp_buffer_max_size=3)
        sink = RedisSink(config)

        # 填满缓存
        for i in range(3):
            sink._store_to_temp_buffer(_make_record(f"log-{i}", seq=i))
        assert len(sink._temp_buffer) == 3

        # 溢出时只丢弃 1 条（最旧的 log-0）
        sink._store_to_temp_buffer(_make_record("overflow", seq=99))
        assert len(sink._temp_buffer) == 3
        contents = [r.content for r in sink._temp_buffer]
        assert contents == ["log-1", "log-2", "overflow"]

    def test_deque_not_full_no_drop(self) -> None:
        """未满时不应丢弃任何日志"""
        config = PlumelogSettings(app_name="test", env="test", temp_buffer_max_size=10)
        sink = RedisSink(config)

        sink._store_to_temp_buffer(_make_record("a"))
        sink._store_to_temp_buffer(_make_record("b"))
        assert len(sink._temp_buffer) == 2


# =============================================================================
# Fix 2: print() 限频告警
# =============================================================================


class TestRateLimitedWarn:
    """验证 _rate_limited_warn 限频机制"""

    def test_first_call_prints(self) -> None:
        """首次告警应立即输出"""
        config = PlumelogSettings(app_name="test", env="test")
        sink = RedisSink(config)
        sink._warn_interval = 10.0

        captured = StringIO()
        with patch("sys.stderr", captured):
            sink._rate_limited_warn("test_cat", "[TEST] hello")

        assert "[TEST] hello" in captured.getvalue()

    def test_subsequent_calls_suppressed(self) -> None:
        """间隔内的后续调用应被抑制"""
        config = PlumelogSettings(app_name="test", env="test")
        sink = RedisSink(config)
        sink._warn_interval = 60.0  # 60 秒内不重复打印

        captured = StringIO()
        with patch("sys.stderr", captured):
            sink._rate_limited_warn("cat", "[TEST] msg")
            sink._rate_limited_warn("cat", "[TEST] msg")
            sink._rate_limited_warn("cat", "[TEST] msg")

        # 60 秒内只应打印 1 次
        output = captured.getvalue()
        assert output.count("[TEST] msg") == 1

    def test_different_categories_independent(self) -> None:
        """不同类别的告警应独立计数"""
        config = PlumelogSettings(app_name="test", env="test")
        sink = RedisSink(config)
        sink._warn_interval = 60.0

        captured = StringIO()
        with patch("sys.stderr", captured):
            sink._rate_limited_warn("cat_a", "[A] first")
            sink._rate_limited_warn("cat_b", "[B] first")

        output = captured.getvalue()
        assert "[A] first" in output
        assert "[B] first" in output

    def test_counter_shown_when_accumulated(self) -> None:
        """超过间隔后打印应显示累计次数"""
        config = PlumelogSettings(app_name="test", env="test")
        sink = RedisSink(config)
        sink._warn_interval = 0.0  # 每次都允许打印

        captured = StringIO()
        with patch("sys.stderr", captured):
            # 第一次打印
            sink._rate_limited_warn("cat", "[TEST] msg")
            # 手动增加计数器以模拟抑制期间的累积
            sink._warn_counters["cat"] = 5
            sink._rate_limited_warn("cat", "[TEST] msg")

        output = captured.getvalue()
        assert "累计 6 次" in output


# =============================================================================
# Fix 3: json.dumps 移到 retry 循环外
# =============================================================================


class TestJsonSerializationOutsideRetry:
    """验证 json.dumps 只执行一次，不随 retry 重复"""

    @pytest.mark.asyncio
    async def test_send_log_records_serializes_once(
        self, test_config: PlumelogSettings
    ) -> None:
        """批量发送重试时不应重复序列化"""
        import json

        client = AsyncRedisClient(test_config)
        client._connected = True

        # 模拟 Redis：第一次失败（触发重试），第二次成功
        call_count = 0

        class FlakyRedis:
            async def lpush(self, key: str, *values: str) -> int:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ConnectionError("first attempt fails")
                return len(values)

        client.redis = FlakyRedis()  # type: ignore[assignment]
        client.retry_count = 2
        client.retry_delay = 0.01

        # Mock cleanup 避免真实清理
        client._cleanup_on_error = AsyncMock()  # type: ignore[method-assign]
        client.connect = AsyncMock()  # type: ignore[method-assign]

        # 追踪 json.dumps 调用次数
        original_dumps = json.dumps
        dumps_count = 0

        def counting_dumps(*args: Any, **kwargs: Any) -> str:
            nonlocal dumps_count
            dumps_count += 1
            return original_dumps(*args, **kwargs)

        records = [_make_record(f"msg-{i}") for i in range(3)]

        with patch("plumelog_loguru.redis_client.json.dumps", counting_dumps):
            result = await client.send_log_records(records)

        # 3 条记录只应序列化 3 次（在 retry 循环外），而不是 6 次
        assert dumps_count == 3, f"json.dumps 调用了 {dumps_count} 次，期望 3 次"
        assert result is True

    @pytest.mark.asyncio
    async def test_send_log_record_serializes_once(
        self, test_config: PlumelogSettings
    ) -> None:
        """单条发送重试时也不应重复序列化"""
        import json

        client = AsyncRedisClient(test_config)
        client._connected = True

        call_count = 0

        class FlakyRedis:
            async def lpush(self, key: str, *values: str) -> int:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ConnectionError("first attempt fails")
                return 1

        client.redis = FlakyRedis()  # type: ignore[assignment]
        client.retry_count = 2
        client.retry_delay = 0.01
        client._cleanup_on_error = AsyncMock()  # type: ignore[method-assign]
        client.connect = AsyncMock()  # type: ignore[method-assign]

        original_dumps = json.dumps
        dumps_count = 0

        def counting_dumps(*args: Any, **kwargs: Any) -> str:
            nonlocal dumps_count
            dumps_count += 1
            return original_dumps(*args, **kwargs)

        record = _make_record("single")

        with patch("plumelog_loguru.redis_client.json.dumps", counting_dumps):
            result = await client.send_log_record(record)

        assert dumps_count == 1, f"json.dumps 调用了 {dumps_count} 次，期望 1 次"
        assert result is True


# =============================================================================
# Fix 4: 异常消息脱敏
# =============================================================================


class TestErrorSanitization:
    """验证异常消息中的 Redis 密码被脱敏"""

    def test_sanitize_error_masks_password(self) -> None:
        """异常消息中包含密码时应替换为 ***"""
        config = PlumelogSettings(
            app_name="test",
            env="test",
            redis_password="SuperSecret123!",
        )
        client = AsyncRedisClient(config)

        error = ConnectionError(
            "Error connecting to redis://localhost:6379 with password SuperSecret123!"
        )
        sanitized = client._sanitize_error(error)

        assert "SuperSecret123!" not in sanitized
        assert "***" in sanitized

    def test_sanitize_error_no_password(self) -> None:
        """无密码时应原样返回"""
        config = PlumelogSettings(app_name="test", env="test")
        client = AsyncRedisClient(config)

        error = ConnectionError("Connection refused")
        sanitized = client._sanitize_error(error)

        assert sanitized == "Connection refused"

    @pytest.mark.asyncio
    async def test_handle_send_error_uses_sanitized_output(
        self, test_config: PlumelogSettings, monkeypatch: MonkeyPatch
    ) -> None:
        """_handle_send_error 的输出不应包含明文密码"""
        config = PlumelogSettings(
            app_name="test",
            env="test",
            redis_password="MyP@ssw0rd",
        )
        client = AsyncRedisClient(config)
        client._connected = True
        client._cleanup_on_error = AsyncMock()  # type: ignore[method-assign]

        captured = StringIO()
        with patch("sys.stderr", captured):
            await client._handle_send_error(
                ConnectionError("failed with MyP@ssw0rd in url"),
                attempt=2,
                log_count=1,
            )

        output = captured.getvalue()
        assert "MyP@ssw0rd" not in output
        assert "***" in output


# =============================================================================
# Fix 5: disconnect() 资源清理加固
# =============================================================================


class TestDisconnectHardening:
    """验证 disconnect() 的加固行为"""

    @pytest.mark.asyncio
    async def test_disconnect_clears_state_in_finally(
        self, test_config: PlumelogSettings
    ) -> None:
        """disconnect 后 redis/pool 应为 None，_connected 应为 False"""
        client = AsyncRedisClient(test_config)
        client._connected = True
        client.redis = AsyncMock()
        client.pool = AsyncMock()

        await client.disconnect()

        assert client.redis is None
        assert client.pool is None
        assert client._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_pool_closed_even_when_redis_close_fails(
        self, test_config: PlumelogSettings
    ) -> None:
        """redis.aclose() 失败时 pool.aclose() 仍应被调用"""
        client = AsyncRedisClient(test_config)
        client._connected = True

        mock_redis = AsyncMock()
        mock_redis.aclose.side_effect = RuntimeError("redis close failed")
        mock_pool = AsyncMock()

        client.redis = mock_redis
        client.pool = mock_pool

        await client.disconnect()

        # pool 应该仍然被关闭
        mock_pool.aclose.assert_called_once()
        # 状态应被清理
        assert client.redis is None
        assert client.pool is None
        assert client._connected is False
