"""Loguru Redis Sink实现

提供Loguru的自定义sink，负责接收日志记录，转换为Plumelog格式，
并异步发送到Redis。支持异步操作、批量处理和错误处理。
"""

import asyncio
from typing import Any, Callable, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from loguru import Record
else:
    Record = Any


import threading
from collections import deque
from concurrent.futures import Future
from typing import Coroutine, TypeVar


class LogSink(Protocol):
    """Loguru sink协议定义"""

    def __call__(self, message: Record) -> None:
        """处理日志消息"""
        ...


from .config import PlumelogSettings
from .extractor import FieldExtractor
from .models import LogRecord
from .redis_client import AsyncRedisClient


T = TypeVar("T")


class _AsyncRuntime:
    """管理 RedisSink 专用事件循环的后台线程"""

    def __init__(self, thread_name: str = "RedisSinkLoop") -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=thread_name,
            daemon=True,
        )
        # 使用事件通知确保线程启动后再返回，避免投递协程时 loop 尚未就绪
        self._ready = threading.Event()
        self._stopped = False
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        if self._stopped:
            raise RuntimeError("事件循环已停止")
        # run_coroutine_threadsafe 负责跨线程调度协程并返回 Future
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        # 通过 call_soon_threadsafe 安全停止事件循环
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()
        self.loop.close()


class RedisSink:
    """Loguru Redis Sink

    作为Loguru的自定义sink，负责接收日志记录，转换为Plumelog格式，
    并异步发送到Redis。通过内部队列和后台任务实现解耦，避免阻塞主线程。
    """

    def __init__(self, config: PlumelogSettings | None = None) -> None:
        """初始化Redis Sink

        Args:
            config: Plumelog配置对象，如果为None则使用默认配置
        """
        self.config = config or PlumelogSettings()
        self.field_extractor = FieldExtractor()
        self.redis_client = AsyncRedisClient(self.config)

        # 异步组件相关属性
        self._log_queue: asyncio.Queue[LogRecord] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._running = False
        self._initialized = False
        self._init_lock: asyncio.Lock | None = None

        # 临时缓存队列，用于存储初始化前的日志
        self._temp_buffer: deque[LogRecord] = deque(
            maxlen=self.config.temp_buffer_max_size
        )
        self._temp_buffer_lock = threading.Lock()

        # 专用运行时为多线程环境提供统一的事件循环与调度能力
        self._runtime: _AsyncRuntime | None = _AsyncRuntime()
        self._closing = False

    async def _ensure_initialized(self) -> None:
        """确保异步组件已初始化"""
        if self._initialized:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._initialized:
                return

            # 在专用事件循环内初始化队列和消费者任务
            self._log_queue = asyncio.Queue(maxsize=self.config.queue_max_size)
            self._running = True
            self._consumer_task = asyncio.create_task(
                self._log_consumer(),
                name="RedisSinkConsumer",
            )

            # 初始化阶段需要把临时缓存的历史日志尽快回放
            await self._transfer_temp_buffer_to_queue()

            self._initialized = True
            print("[RedisSink] 异步组件初始化完成")

    def __call__(self, message: Record) -> None:
        """Loguru sink调用接口（同步入口）

        这是Loguru调用的主要接口，需要处理同步到异步的转换。

        Args:
            message: Loguru日志消息对象
        """
        try:
            log_record = self._convert_to_log_record(message)
        except Exception as exc:  # noqa: BLE001
            print(f"[RedisSink] 处理日志时发生错误: {exc}")
            try:
                message_text = str(
                    getattr(message, "record", {}).get("message", message)
                )
                print(f"[RedisSink] 降级输出: {message_text}")
            except Exception:  # noqa: BLE001
                print(f"[RedisSink] 降级输出: {str(message)}")
            return

        # 关闭流程已启动时不再提交到事件循环，改为暂存在临时缓存
        if self._closing or self._runtime is None:
            self._store_to_temp_buffer(log_record)
            return

        try:
            future = self._runtime.submit(self._async_handle_log(log_record))
            future.add_done_callback(self._log_future_exception)
        except Exception as exc:  # noqa: BLE001
            print(f"[RedisSink] 提交日志处理任务失败: {exc}")
            self._store_to_temp_buffer(log_record)

    async def _run_in_runtime(self, coro: Coroutine[Any, Any, T]) -> T:
        """在线程事件循环中执行协程并返回结果"""
        if self._runtime is None:
            raise RuntimeError("事件循环已停止，无法提交任务")
        # wrap_future 允许在当前协程中等待跨线程执行结果
        future = self._runtime.submit(coro)
        return await asyncio.wrap_future(future)

    @staticmethod
    def _log_future_exception(future: Future[Any]) -> None:
        """记录后台任务中的异常"""
        try:
            exception = future.exception()
        except Exception as exc:  # noqa: BLE001
            print(f"[RedisSink] 检查后台任务状态失败: {exc}")
            return

        if exception:
            # 后台异常不应该悄无声息，需要打印以便排查
            print(f"[RedisSink] 后台处理日志抛出异常: {exception}")

    async def _flush_temp_buffer_to_redis(self) -> None:
        """在关闭时将临时缓存发送到Redis"""
        with self._temp_buffer_lock:
            buffered_logs = list(self._temp_buffer)
            self._temp_buffer.clear()

        if not buffered_logs:
            return

        print(f"[RedisSink] 发送剩余的 {len(buffered_logs)} 条临时缓存日志...")
        # 关闭阶段直接批量投递到 Redis，避免数据遗失
        await self.redis_client.send_log_records(buffered_logs)

    async def _async_handle_log(self, log_record: LogRecord) -> None:
        """异步处理日志记录

        Args:
            log_record: 日志记录对象
        """
        try:
            await self._ensure_initialized()

            if not self._log_queue:
                self._store_to_temp_buffer(log_record)
                return

            await self._log_queue.put(log_record)

        except Exception as e:
            print(f"[RedisSink] 异步处理日志失败: {e}")
            # 回退到临时缓存等待后续重试
            self._store_to_temp_buffer(log_record)

    def _store_to_temp_buffer(self, log_record: LogRecord) -> None:
        """将日志存储到临时缓存

        Args:
            log_record: 日志记录对象
        """
        with self._temp_buffer_lock:
            if len(self._temp_buffer) >= self.config.temp_buffer_max_size:
                # 保留最新日志，丢弃最旧的记录
                self._temp_buffer.popleft()
                print("[RedisSink] 临时缓存已满，移除最老的日志")
            self._temp_buffer.append(log_record)

    async def _transfer_temp_buffer_to_queue(self) -> None:
        """将临时缓存的日志转移到正式队列"""
        if not self._log_queue:
            return

        with self._temp_buffer_lock:
            buffered_logs = list(self._temp_buffer)
            self._temp_buffer.clear()

        if not buffered_logs:
            return

        transferred_count = 0
        for log_record in buffered_logs:
            try:
                await self._log_queue.put(log_record)
                transferred_count += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[RedisSink] 转移临时缓存日志失败: {exc}")
                # 将未能转移的日志重新放回缓存，避免直接丢失
                with self._temp_buffer_lock:
                    remaining = buffered_logs[transferred_count:]
                    for item in remaining:
                        if len(self._temp_buffer) >= self.config.temp_buffer_max_size:
                            self._temp_buffer.popleft()
                        self._temp_buffer.append(item)
                break

        if transferred_count > 0:
            print(f"[RedisSink] 已将 {transferred_count} 条临时缓存日志转移到正式队列")

    async def _log_consumer(self) -> None:
        """后台消费者任务，持续从队列中获取日志并发送到Redis"""
        assert self._log_queue is not None, "队列未初始化"

        while self._running or not self._log_queue.empty():
            try:
                log_record = await asyncio.wait_for(
                    self._log_queue.get(), timeout=self.config.batch_interval_seconds
                )

                batch = [log_record]
                while (
                    len(batch) < self.config.batch_size and not self._log_queue.empty()
                ):
                    try:
                        batch.append(self._log_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                success = await self.redis_client.send_log_records(batch)

                if success:
                    for _ in batch:
                        self._log_queue.task_done()
                else:
                    for _ in batch:
                        self._log_queue.task_done()
                    # 发送失败时重新入队，等待下一轮重试
                    for log in batch:
                        try:
                            await self._log_queue.put(log)
                        except asyncio.QueueFull:
                            print("[RedisSink] 队列已满，无法重新排队失败的日志")
                            break

            except asyncio.TimeoutError:
                if not self._running:
                    break
                continue

            except Exception as e:  # noqa: BLE001
                print(f"[RedisSink] 消费者任务异常: {e}")
                # 留出冷却时间，避免在异常状态下频繁重试
                await asyncio.sleep(5)

    def _convert_to_log_record(self, message: Record) -> LogRecord:
        """转换Loguru消息为LogRecord对象

        Args:
            message: Loguru日志消息对象

        Returns:
            LogRecord对象
        """
        # 获取调用者信息
        caller_info = self.field_extractor.get_caller_info(depth=3)

        # 获取系统信息
        system_info = self.field_extractor.get_system_info()

        # 获取时间信息
        import datetime

        record_dict = getattr(message, "record", {})
        log_time = record_dict.get("time")

        # 如果 log_time 为 None，使用当前时间
        if log_time is None:
            log_time = datetime.datetime.now()

        # 构建LogRecord对象
        return LogRecord(
            server_name=system_info.server_name,
            app_name=self.config.app_name,
            env=self.config.env,
            method=caller_info.method_name_safe,
            content=str(record_dict.get("message", "")),
            log_level=getattr(record_dict.get("level", {}), "name", "INFO"),
            class_name=caller_info.class_name_safe,
            thread_name=system_info.thread_name,
            seq=self.field_extractor.get_next_seq(),
            date_time=self.field_extractor.format_datetime(log_time),
            dt_time=self.field_extractor.get_timestamp_ms(log_time),
        )

    async def _async_close(self) -> None:
        """在专用事件循环中执行资源回收"""
        if not self._runtime:
            return

        print("[RedisSink] 正在关闭...")
        if not self._initialized:
            await self._flush_temp_buffer_to_redis()
            await self.redis_client.disconnect()
            self._initialized = False
            return

        self._running = False

        if self._log_queue:
            await self._log_queue.join()

        if self._consumer_task and not self._consumer_task.done():
            try:
                await asyncio.wait_for(self._consumer_task, timeout=10.0)
            except asyncio.TimeoutError:
                print("[RedisSink] 消费者任务超时，强制取消")
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except asyncio.CancelledError:
                    pass

        await self._flush_temp_buffer_to_redis()

        if self._log_queue and not self._log_queue.empty():
            remaining_logs = []
            while not self._log_queue.empty():
                try:
                    remaining_logs.append(self._log_queue.get_nowait())
                    self._log_queue.task_done()
                except asyncio.QueueEmpty:
                    break

            if remaining_logs:
                print(f"[RedisSink] 发送剩余的 {len(remaining_logs)} 条队列日志...")
                await self.redis_client.send_log_records(remaining_logs)

        await self.redis_client.disconnect()

        self._initialized = False
        self._log_queue = None
        self._consumer_task = None

        print("[RedisSink] 已成功关闭")

    async def close(self) -> None:
        """关闭Redis Sink，停止后台任务并清理资源"""
        if self._closing:
            return

        self._closing = True

        try:
            # 统一在专用事件循环中完成所有清理动作
            await self._run_in_runtime(self._async_close())
        finally:
            if self._runtime is not None:
                self._runtime.stop()
                self._runtime = None

        print("[RedisSink] 关闭流程结束")

    async def __aenter__(self) -> "RedisSink":  # type: ignore
        """异步上下文管理器入口"""
        # 上下文进入时提前初始化，避免在日志产生后才启动消费者
        await self._run_in_runtime(self._ensure_initialized())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        """异步上下文管理器出口"""
        await self.close()


def create_redis_sink(
    config: PlumelogSettings | None = None,
) -> Callable[[Record], None]:
    """创建Redis Sink函数

    提供便捷的工厂函数来创建Redis Sink实例。

    Args:
        config: Plumelog配置对象

    Returns:
        可用于Loguru的sink函数
    """
    return RedisSink(config)
