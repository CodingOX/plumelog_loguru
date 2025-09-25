"""异步Redis客户端模块

提供与Redis的异步连接、数据传输、连接池管理、重试机制和错误处理功能。
使用现代异步编程模式和强类型，确保高性能和可靠性。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError, RedisError

from .config import PlumelogSettings
from .models import LogRecord


class AsyncRedisClient:
    """异步Redis客户端

    负责与Redis的异步连接、数据传输、连接池管理、重试机制和错误处理。
    支持连接池复用和优雅的错误处理，确保网络异常时的稳定性。
    """

    def __init__(self, config: PlumelogSettings) -> None:
        """初始化Redis客户端

        Args:
            config: Plumelog配置对象
        """
        self.config = config
        self.pool: redis.ConnectionPool | None = None
        self.redis: redis.Redis | None = None
        self._connected = False
        self._ever_connected = False

        # 从配置中获取重试参数
        self.retry_count = config.retry_count
        self.retry_delay = config.retry_delay

    async def connect(self) -> None:
        """建立Redis连接和连接池

        Raises:
            ConnectionError: 当无法连接到Redis时
        """
        pid = os.getpid()
        try:
            # 创建连接池
            self.pool = redis.ConnectionPool(
                host=self.config.redis_host,
                port=self.config.redis_port,
                db=self.config.redis_db,
                password=self.config.redis_password,
                max_connections=self.config.max_connections,
                retry_on_timeout=True,
                socket_connect_timeout=self.config.socket_connect_timeout,
                socket_timeout=self.config.socket_timeout,
                decode_responses=True,  # 自动解码响应为字符串
            )

            # 创建Redis客户端
            self.redis = redis.Redis(connection_pool=self.pool)

            # 测试连接
            await self.redis.ping()
            self._connected = True
            self._ever_connected = True

            print(
                f"[Plumelog][PID:{pid}] Redis连接成功: "
                f"{self.config.redis_host}:{self.config.redis_port}"
            )

        except Exception as e:
            print(f"[Plumelog][PID:{pid}] Redis连接失败: {e}")
            self._connected = False
            await self._cleanup_on_error()
            raise ConnectionError(f"无法连接到Redis: {e}") from e

    async def disconnect(self) -> None:
        """断开Redis连接并清理资源"""
        pid = os.getpid()
        try:
            if self.redis:
                await self.redis.aclose()
            if self.pool:
                await self.pool.aclose()
            self._connected = False
            print(f"[Plumelog][PID:{pid}] Redis连接已断开")
        except Exception as e:
            print(f"[Plumelog][PID:{pid}] 断开Redis连接时发生错误: {e}")

    async def _cleanup_on_error(self) -> None:
        """错误时的清理操作"""
        try:
            if self.redis:
                await self.redis.aclose()
            if self.pool:
                await self.pool.aclose()
        except Exception:
            pass  # 忽略清理时的错误
        finally:
            self.redis = None
            self.pool = None
            self._connected = False

    @property
    def is_connected(self) -> bool:
        """检查Redis连接状态

        Returns:
            连接状态
        """
        return self._connected and self.redis is not None

    async def send_log_record(
        self, log_record: LogRecord, key: str | None = None
    ) -> bool:
        """发送单条日志记录到Redis

        Args:
            log_record: 日志记录对象
            key: Redis键名，默认使用配置中的redis_key

        Returns:
            发送是否成功
        """
        pid = os.getpid()
        redis_key = key or self.config.redis_key

        for attempt in range(self.retry_count):
            try:
                # 检查连接状态
                if not self.is_connected:
                    status_text = (
                        "Redis连接断开，尝试重新连接..."
                        if self._ever_connected
                        else "Redis尚未建立连接，正在初始化..."
                    )
                    print(f"[Plumelog][PID:{pid}] {status_text}")
                    await self.connect()

                assert self.redis is not None, "Redis客户端应该已连接"

                # 将日志记录转换为JSON字符串
                log_json = json.dumps(
                    log_record.to_dict(), ensure_ascii=False, separators=(",", ":")
                )

                # 发送单条日志
                result: int = await self.redis.lpush(redis_key, log_json)  # type: ignore[misc]
                if result == 0:
                    raise RedisError("Redis lpush返回结果为0")

                return True

            except Exception as e:
                await self._handle_send_error(e, attempt, 1)
                if attempt == self.retry_count - 1:
                    return False

        return False

    async def send_log_records(
        self, log_records: list[LogRecord], key: str | None = None
    ) -> bool:
        """批量发送日志记录到Redis

        Args:
            log_records: 日志记录列表
            key: Redis键名，默认使用配置中的redis_key

        Returns:
            发送是否成功
        """
        if not log_records:
            return True

        pid = os.getpid()
        redis_key = key or self.config.redis_key

        for attempt in range(self.retry_count):
            try:
                # 检查连接状态
                if not self.is_connected:
                    status_text = (
                        "Redis连接断开，尝试重新连接..."
                        if self._ever_connected
                        else "Redis尚未建立连接，正在初始化..."
                    )
                    print(f"[Plumelog][PID:{pid}] {status_text}")
                    await self.connect()

                assert self.redis is not None, "Redis客户端应该已连接"

                # 单条日志优化：直接处理
                if len(log_records) == 1:
                    return await self.send_log_record(log_records[0], key)

                # 批量发送日志
                async with self.redis.pipeline() as pipe:
                    for log_record in log_records:
                        log_json = json.dumps(
                            log_record.to_dict(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        pipe.lpush(redis_key, log_json)

                    await pipe.execute()

                return True

            except Exception as e:
                await self._handle_send_error(e, attempt, len(log_records))
                if attempt == self.retry_count - 1:
                    return False

        return False

    async def _handle_send_error(
        self, error: Exception, attempt: int, log_count: int
    ) -> None:
        """处理发送错误的通用逻辑

        Args:
            error: 发生的异常
            attempt: 当前尝试次数（从0开始）
            log_count: 发送的日志数量
        """
        pid = os.getpid()
        print(
            f"[Plumelog][PID:{pid}] Redis发送失败 (尝试 {attempt + 1}/{self.retry_count}): {error}"
        )

        # 如果是连接错误，标记为未连接状态
        if isinstance(error, (ConnectionError, OSError)):
            await self._cleanup_on_error()

        if attempt < self.retry_count - 1:
            # 指数退避重试
            delay = self.retry_delay * (2**attempt)
            print(f"[Plumelog][PID:{pid}] 等待 {delay:.1f} 秒后重试...")
            await asyncio.sleep(delay)
        else:
            print(f"[Plumelog][PID:{pid}] Redis发送最终失败，丢失 {log_count} 条日志")

    async def __aenter__(self) -> AsyncRedisClient:
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        """异步上下文管理器出口"""
        await self.disconnect()
