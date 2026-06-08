"""Loguru Redis Sink实现

提供Loguru的自定义sink，负责接收日志记录，转换为Plumelog格式，
并异步发送到Redis。支持异步操作、批量处理和错误处理。
"""

import asyncio
import atexit
import datetime
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from .config import PlumelogSettings
from .extractor import FieldExtractor
from .models import LogRecord
from .redis_client import AsyncRedisClient

if TYPE_CHECKING:
    from loguru import Record
else:
    Record = Any


class LogSink(Protocol):
    """Loguru sink协议定义"""

    def __call__(self, message: Record) -> None:
        """处理日志消息"""
        ...


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

        # 事件循环线程在构造阶段就准备好，避免首次写日志时触发 asyncio
        # 内部调试日志并在第三方 logging -> Loguru 桥接场景下产生 sink 重入。
        # 这里只提前准备 runtime；Redis 连接和消费者任务仍保持懒初始化。
        self._runtime: _AsyncRuntime | None = _AsyncRuntime()
        self._runtime_lock = threading.Lock()
        self._closing = False
        self._pending_submit_limit = (
            self.config.queue_max_size + self.config.temp_buffer_max_size
        )
        self._pending_submit_semaphore = threading.BoundedSemaphore(
            self._pending_submit_limit
        )
        # 仅首次真正使用时才注册 atexit 兜底，避免构造即注册
        self._atexit_registered = False

        # 限频告警配置：高频路径的告警每 _warn_interval 秒最多打印一次，
        # 避免故障/积压时 print() 产生 I/O 风暴
        self._warn_counters: dict[str, int] = {}
        self._warn_last_time: dict[str, float] = {}
        self._warn_interval: float = 10.0

    def _rate_limited_warn(self, category: str, message: str) -> None:
        """限频告警输出：同类告警每 _warn_interval 秒最多打印一次，累计触发次数

        用于替代故障/积压路径中的高频 print()，避免 I/O 风暴。
        """
        now = time.monotonic()
        self._warn_counters[category] = self._warn_counters.get(category, 0) + 1
        # 默认使用 -inf 而非 0.0，确保首次调用时 now - (-inf) = +inf >= interval，
        # 不受 time.monotonic() 绝对值影响（CI 容器启动时间可能 < _warn_interval）
        last = self._warn_last_time.get(category, float("-inf"))
        if now - last >= self._warn_interval:
            count = self._warn_counters[category]
            suffix = f" (累计 {count} 次)" if count > 1 else ""
            print(f"{message}{suffix}", file=sys.stderr)
            self._warn_last_time[category] = now
            self._warn_counters[category] = 0

    def _ensure_runtime(self) -> _AsyncRuntime:
        """按需启动专用事件循环，避免未使用的 sink 占用线程。"""
        if self._runtime is None:
            with self._runtime_lock:
                if self._runtime is None:
                    self._runtime = _AsyncRuntime()
        return self._runtime

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
            print("[RedisSink] 异步组件初始化完成", file=sys.stderr)

    def __call__(self, message: Record) -> None:
        """Loguru sink调用接口（同步入口）

        这是Loguru调用的主要接口，需要处理同步到异步的转换。

        Args:
            message: Loguru日志消息对象
        """
        try:
            log_record = self._convert_to_log_record(message)
        except Exception as exc:  # noqa: BLE001
            print(f"[RedisSink] 处理日志时发生错误: {exc}", file=sys.stderr)
            try:
                message_text = str(
                    getattr(message, "record", {}).get("message", message)
                )
                print(f"[RedisSink] 降级输出: {message_text}", file=sys.stderr)
            except Exception:  # noqa: BLE001
                print(f"[RedisSink] 降级输出: {str(message)}", file=sys.stderr)
            return

        # 关闭流程已启动时不再提交到事件循环，改为暂存在临时缓存
        if self._closing:
            self._store_to_temp_buffer(log_record)
            return

        # 首次真正使用时注册 atexit 兜底：进程正常退出时尽力 flush 剩余日志。
        # 延迟注册是为了避免仅构造 sink 而不使用时也占用 atexit 槽位。
        if not self._atexit_registered:
            self._atexit_registered = True
            atexit.register(self._sync_close_on_exit)

        try:
            if not self._pending_submit_semaphore.acquire(blocking=False):
                self._rate_limited_warn(
                    "backpressure",
                    "[RedisSink] 后台任务积压过高，日志写入临时缓存",
                )
                self._store_to_temp_buffer(log_record)
                return

            future = self._ensure_runtime().submit(self._async_handle_log(log_record))
            # 使用预绑定实例方法而非每次创建闭包，避免高吞吐下大量短生命周期函数对象
            future.add_done_callback(self._on_submit_done)
        except Exception as exc:  # noqa: BLE001
            # 关键：acquire 已成功但 submit 失败时必须归还许可。
            # 若不 release，许可会持续流失，最终导致所有日志退化到
            # temp_buffer（系统假死）。
            self._pending_submit_semaphore.release()
            self._rate_limited_warn(
                "submit_fail",
                f"[RedisSink] 提交日志处理任务失败: {exc}",
            )
            self._store_to_temp_buffer(log_record)

    def _sync_close_on_exit(self) -> None:
        """进程退出时的同步兜底清理（atexit 回调，非 async）

        此方法只在进程正常退出且用户未手动调用 close() 时有效。
        向已有后台事件循环投递关闭协程并同步等待完成，尽力 flush 剩余日志。
        """
        if self._closing:
            return  # 已手动关闭，跳过
        try:
            if self._runtime and not self._runtime._stopped:
                # 向已有的后台事件循环投递关闭协程并等待（最多等 5 秒）
                future = asyncio.run_coroutine_threadsafe(
                    self._async_close(), self._runtime.loop
                )
                future.result(timeout=5.0)
        except Exception as e:
            print(f"[RedisSink] atexit 清理失败: {e}", file=sys.stderr)
        finally:
            if self._runtime and not self._runtime._stopped:
                self._runtime.stop()

    def _on_submit_done(self, done_future: "Future[Any]") -> None:
        """submit 完成回调：就此属方法预绑定，避免每条日志创建闭包对象

        预绑定 vs 闭包的区别：闭包在每次调用时分配新函数对象，高吸吐下增加 GC 压力；
        实例方法在对象生命周期内只存在一份，不产生额外分配。
        """
        self._pending_submit_semaphore.release()
        self._log_future_exception(done_future)

    async def _run_in_runtime(self, coro: Coroutine[Any, Any, T]) -> T:
        """在线程事件循环中执行协程并返回结果"""
        # wrap_future 允许在当前协程中等待跨线程执行结果
        future = self._ensure_runtime().submit(coro)
        return await asyncio.wrap_future(future)

    @staticmethod
    def _log_future_exception(future: Future[Any]) -> None:
        """记录后台任务中的异常"""
        try:
            exception = future.exception()
        except Exception as exc:  # noqa: BLE001
            print(f"[RedisSink] 检查后台任务状态失败: {exc}", file=sys.stderr)
            return

        if exception:
            # 后台异常不应该悄无声息，需要打印以便排查
            print(
                f"[RedisSink] 后台处理日志抛出异常: {exception}",
                file=sys.stderr,
            )

    async def _flush_temp_buffer_to_redis(self) -> None:
        """在关闭时将临时缓存发送到Redis"""
        with self._temp_buffer_lock:
            buffered_logs = list(self._temp_buffer)
            self._temp_buffer.clear()

        if not buffered_logs:
            return

        n = len(buffered_logs)
        print(
            f"[RedisSink] 发送剩余的 {n} 条临时缓存日志...",
            file=sys.stderr,
        )
        # 关闭阶段只做最后一次尽力投递；失败时必须显式暴露丢弃数量。
        success = await self.redis_client.send_log_records(buffered_logs)
        if not success:
            print(
                f"[RedisSink] 临时缓存发送失败，丢弃 {n} 条日志",
                file=sys.stderr,
            )

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
            self._rate_limited_warn(
                "async_handle_fail",
                f"[RedisSink] 异步处理日志失败: {e}",
            )
            # 回退到临时缓存等待后续重试
            self._store_to_temp_buffer(log_record)

    def _store_to_temp_buffer(self, log_record: LogRecord) -> None:
        """将日志存储到临时缓存

        deque(maxlen=N) 会在满时自动淘汰最旧记录，无需手动 popleft()。

        Args:
            log_record: 日志记录对象
        """
        with self._temp_buffer_lock:
            is_full = len(self._temp_buffer) >= self.config.temp_buffer_max_size
            # deque(maxlen=N) 自动处理溢出，无需手动 popleft
            self._temp_buffer.append(log_record)
            if is_full:
                self._rate_limited_warn(
                    "temp_buffer_full",
                    "[RedisSink] 临时缓存已满，最旧日志被自动淘汰",
                )

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
                print(f"[RedisSink] 转移临时缓存日志失败: {exc}", file=sys.stderr)
                # 将未能转移的日志重新放回缓存，避免直接丢失
                with self._temp_buffer_lock:
                    remaining = buffered_logs[transferred_count:]
                    for item in remaining:
                        if len(self._temp_buffer) >= self.config.temp_buffer_max_size:
                            self._temp_buffer.popleft()
                        self._temp_buffer.append(item)
                break

        if transferred_count > 0:
            print(
                f"[RedisSink] 已将 {transferred_count} 条临时缓存日志转移到正式队列",
                file=sys.stderr,
            )

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

                for _ in batch:
                    self._log_queue.task_done()

                if not success:
                    # AsyncRedisClient 内部已经执行有限重试；这里不再回灌队列，
                    # 避免 Redis 长期不可用时形成无限重试并阻塞 close()。
                    self._rate_limited_warn(
                        "redis_send_fail",
                        f"[RedisSink] Redis发送最终失败，丢弃 {len(batch)} 条日志",
                    )

            except asyncio.TimeoutError:
                if not self._running:
                    break
                continue

            except Exception as e:  # noqa: BLE001
                print(f"[RedisSink] 消费者任务异常: {e}", file=sys.stderr)
                # 留出冷却时间，避免在异常状态下频繁重试
                await asyncio.sleep(5)

    def _convert_to_log_record(self, message: Record) -> LogRecord:
        """转换Loguru消息为LogRecord对象

        Args:
            message: Loguru日志消息对象

        Returns:
            LogRecord对象
        """
        record_dict = getattr(message, "record", {})

        # 优先使用 Loguru 已解析的调用者字段，避免热路径重复 inspect。
        # 快速路径：两个字段都存在时，完全跳过 inspect.currentframe() 调用。
        # 慢速路径：仅当 Loguru 未提供字段时，才回退到 get_caller_info()（惰性求值）。
        raw_function = record_dict.get("function")
        raw_name = record_dict.get("name")

        if raw_function and raw_name:
            # 快速路径：Loguru 已提供完整调用者信息，无需 inspect
            method = str(raw_function)
            class_name = str(raw_name)
        else:
            # 慢速路径：Loguru 字段缺失，回退到 inspect.currentframe() 栈解析
            caller_info = self.field_extractor.get_caller_info(depth=3)
            method = str(raw_function or caller_info.method_name_safe)
            class_name = str(raw_name or caller_info.class_name_safe)

        # 获取系统信息
        system_info = self.field_extractor.get_system_info()

        log_time = record_dict.get("time")

        # 如果 log_time 为 None，使用当前时间
        if log_time is None:
            log_time = datetime.datetime.now()

        # 构建LogRecord对象
        return LogRecord(
            server_name=system_info.server_name,
            app_name=self.config.app_name,
            env=self.config.env,
            method=method,
            content=str(record_dict.get("message", "")),
            log_level=getattr(record_dict.get("level", {}), "name", "INFO"),
            class_name=class_name,
            thread_name=system_info.thread_name,
            seq=self.field_extractor.get_next_seq(),
            date_time=self.field_extractor.format_datetime(log_time),
            dt_time=self.field_extractor.get_timestamp_ms(log_time),
        )

    async def _async_close(self) -> None:
        """在专用事件循环中执行资源回收"""
        if not self._runtime:
            return

        print("[RedisSink] 正在关闭...", file=sys.stderr)
        if not self._initialized:
            await self._flush_temp_buffer_to_redis()
            await self.redis_client.disconnect()
            self._initialized = False
            return

        self._running = False

        if self._log_queue:
            try:
                await asyncio.wait_for(self._log_queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                print(
                    "[RedisSink] 等待队列清空超时，将继续关闭并清理剩余日志",
                    file=sys.stderr,
                )

        if self._consumer_task and not self._consumer_task.done():
            try:
                await asyncio.wait_for(self._consumer_task, timeout=10.0)
            except asyncio.TimeoutError:
                print("[RedisSink] 消费者任务超时，强制取消", file=sys.stderr)
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
                n = len(remaining_logs)
                print(
                    f"[RedisSink] 发送剩余的 {n} 条队列日志...",
                    file=sys.stderr,
                )
                success = await self.redis_client.send_log_records(remaining_logs)
                if not success:
                    print(
                        "[RedisSink] 剩余队列日志发送失败，"
                        f"丢弃 {len(remaining_logs)} 条日志",
                        file=sys.stderr,
                    )

        await self.redis_client.disconnect()

        self._initialized = False
        self._log_queue = None
        self._consumer_task = None

        print("[RedisSink] 已成功关闭", file=sys.stderr)

    async def close(self) -> None:
        """关闭Redis Sink，停止后台任务并清理资源"""
        if self._closing:
            return

        self._closing = True

        if self._runtime is None:
            with self._temp_buffer_lock:
                has_buffered_logs = bool(self._temp_buffer)
            if not has_buffered_logs:
                return

        try:
            # 统一在专用事件循环中完成所有清理动作
            await self._run_in_runtime(self._async_close())
        finally:
            if self._runtime is not None:
                self._runtime.stop()
                self._runtime = None

        print("[RedisSink] 关闭流程结束", file=sys.stderr)

    async def __aenter__(self) -> "RedisSink":
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
