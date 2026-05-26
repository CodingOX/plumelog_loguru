"""RedisSink 行为测试"""

import asyncio
import datetime
import threading
from types import SimpleNamespace
from typing import Any, cast

from _pytest.monkeypatch import MonkeyPatch

from plumelog_loguru import PlumelogSettings
from plumelog_loguru.models import LogRecord
from plumelog_loguru.redis_sink import RedisSink


class DummyAsyncRedisClient:
    """测试替身：记录发送的日志并统计断开次数"""

    def __init__(self, config: Any) -> None:  # noqa: D401
        self.config = config
        self.sent_records: list[LogRecord] = []
        self.disconnect_calls = 0

    async def send_log_records(
        self, records: list[LogRecord], key: str | None = None
    ) -> bool:
        self.sent_records.extend(records)
        return True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


class AlwaysFailAsyncRedisClient:
    """测试替身：模拟 Redis 持续不可用"""

    def __init__(self, config: Any) -> None:  # noqa: D401
        self.config = config
        self.send_calls = 0
        self.disconnect_calls = 0

    async def send_log_records(
        self, records: list[LogRecord], key: str | None = None
    ) -> bool:
        self.send_calls += 1
        return False

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


def _build_message(content: str) -> SimpleNamespace:
    """构造与 Loguru Record 接口兼容的简易对象"""
    level = SimpleNamespace(name="INFO")
    return SimpleNamespace(
        record={
            "message": content,
            "level": level,
            "time": datetime.datetime.now(),
        }
    )


def _extract_contents(records: list[LogRecord]) -> list[str]:
    return [record.content for record in records]


def test_redis_sink_handles_multi_thread_logs(
    monkeypatch: MonkeyPatch, test_config: PlumelogSettings
) -> None:
    """多线程写入时应由后台事件循环统一消费"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    sink = RedisSink(test_config)

    def worker(thread_id: int) -> None:
        for idx in range(10):
            sink(_build_message(f"thread-{thread_id}-log-{idx}"))  # type: ignore[arg-type]

    threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 关闭时等待后台 loop 完整清空队列
    asyncio.run(sink.close())

    client = cast(DummyAsyncRedisClient, sink.redis_client)
    contents = _extract_contents(client.sent_records)
    assert len(contents) == 30
    assert client.disconnect_calls == 1
    assert all(content.startswith("thread-") for content in contents)


def test_redis_sink_flushes_temp_buffer_on_close(
    monkeypatch: MonkeyPatch, test_config: PlumelogSettings
) -> None:
    """关闭前的临时缓存必须完整写入 Redis"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    sink = RedisSink(test_config)

    # 模拟初始化前积累的缓存
    temp_record = LogRecord(
        server_name="server",
        app_name=test_config.app_name,
        env=test_config.env,
        method="method",
        content="cached-log",
        log_level="INFO",
        class_name="Class",
        thread_name="MainThread",
        seq=1,
        date_time="2024-01-01 00:00:00",
        dt_time=1704067200000,
    )
    sink._store_to_temp_buffer(temp_record)

    asyncio.run(sink.close())

    client = cast(DummyAsyncRedisClient, sink.redis_client)
    contents = _extract_contents(client.sent_records)
    assert "cached-log" in contents
    assert client.disconnect_calls == 1


def test_redis_sink_close_returns_when_redis_send_keeps_failing(
    monkeypatch: MonkeyPatch, test_config: PlumelogSettings
) -> None:
    """Redis 持续失败时 close 不能卡死在 queue.join()"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", AlwaysFailAsyncRedisClient
    )
    config = PlumelogSettings(
        app_name=test_config.app_name,
        env=test_config.env,
        redis_host=test_config.redis_host,
        redis_port=test_config.redis_port,
        redis_db=test_config.redis_db,
        queue_max_size=2,
        batch_size=1,
        batch_interval_seconds=0.01,
    )
    sink = RedisSink(config)

    sink(_build_message("will-fail"))  # type: ignore[arg-type]

    async def close_with_timeout() -> None:
        await asyncio.sleep(0.05)
        await asyncio.wait_for(sink.close(), timeout=1.0)

    asyncio.run(close_with_timeout())

    client = cast(AlwaysFailAsyncRedisClient, sink.redis_client)
    assert client.disconnect_calls == 1


def test_redis_sink_uses_temp_buffer_when_pending_submit_limit_is_reached(
    monkeypatch: MonkeyPatch,
) -> None:
    """后台投递积压达到上限时，应进入临时缓存而不是继续创建任务"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    config = PlumelogSettings(
        app_name="test_app",
        env="test",
        queue_max_size=1,
        temp_buffer_max_size=2,
        batch_interval_seconds=0.01,
    )
    sink = RedisSink(config)

    for _ in range(sink._pending_submit_limit):
        assert sink._pending_submit_semaphore.acquire(blocking=False)

    sink(_build_message("overflow"))  # type: ignore[arg-type]

    assert len(sink._temp_buffer) == 1

    for _ in range(sink._pending_submit_limit):
        sink._pending_submit_semaphore.release()
    asyncio.run(sink.close())


def test_redis_sink_does_not_start_runtime_until_first_use(
    test_config: PlumelogSettings,
) -> None:
    """构造 RedisSink 不应立即启动后台线程和事件循环"""
    sink = RedisSink(test_config)

    assert sink._runtime is None

    asyncio.run(sink.close())


def test_redis_sink_uses_loguru_record_caller_fields(
    monkeypatch: MonkeyPatch, test_config: PlumelogSettings
) -> None:
    """优先使用 Loguru 已解析的调用者字段，避免热路径重复 inspect"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    sink = RedisSink(test_config)
    message = SimpleNamespace(
        record={
            "message": "caller",
            "level": SimpleNamespace(name="INFO"),
            "time": datetime.datetime.now(),
            "function": "handler",
            "name": "app.service",
        }
    )

    record = sink._convert_to_log_record(message)  # type: ignore[arg-type]

    assert record.method == "handler"
    assert record.class_name == "app.service"
    asyncio.run(sink.close())
