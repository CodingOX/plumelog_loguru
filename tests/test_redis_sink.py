"""RedisSink 行为测试"""

import asyncio
import datetime
import threading
from types import SimpleNamespace
from typing import cast

from plumelog_loguru.models import LogRecord
from plumelog_loguru.redis_sink import RedisSink


class DummyAsyncRedisClient:
    """测试替身：记录发送的日志并统计断开次数"""

    def __init__(self, config) -> None:  # noqa: D401
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


def test_redis_sink_handles_multi_thread_logs(monkeypatch, test_config):
    """多线程写入时应由后台事件循环统一消费"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    sink = RedisSink(test_config)

    def worker(thread_id: int) -> None:
        for idx in range(10):
            sink(_build_message(f"thread-{thread_id}-log-{idx}"))  # type: ignore

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


def test_redis_sink_flushes_temp_buffer_on_close(monkeypatch, test_config):
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
