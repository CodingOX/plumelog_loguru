"""系统信息提取器模块

提供系统信息提取功能，包括服务器IP、主机名、线程名、类名、方法名等。
使用强类型和现代Python特性，提供线程安全的序列号生成。
"""

import datetime
import inspect
import socket
import threading

from .models import CallerInfo, SystemInfo


class FieldExtractor:
    """系统信息提取器

    负责提取服务器IP、主机名、线程名、类名、方法名等信息。
    提供线程安全的序列号生成和标准化的时间格式化功能。
    """

    def __init__(self) -> None:
        """初始化字段提取器"""
        self._seq_counter: int = 0
        self._lock: threading.Lock = threading.Lock()
        self._server_name: str | None = None
        self._host_name: str | None = None

    def get_server_name(self) -> str:
        """获取服务器IP地址

        使用缓存机制避免重复获取，提高性能。

        Returns:
            服务器IP地址字符串
        """
        if self._server_name is None:
            try:
                # 创建临时socket连接来获取本机IP
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    # 连接到一个不存在的地址，不会实际发送数据
                    s.connect(("8.8.8.8", 80))
                    self._server_name = s.getsockname()[0]
            except Exception:
                # 如果上述方法失败，使用备用方法
                try:
                    hostname = socket.gethostname()
                    self._server_name = socket.gethostbyname(hostname)
                except Exception:
                    # 最后的备用方案
                    self._server_name = "127.0.0.1"

        assert self._server_name is not None
        return self._server_name

    def get_host_name(self) -> str:
        """获取主机名

        Returns:
            主机名字符串
        """
        if self._host_name is None:
            try:
                self._host_name = socket.gethostname()
            except Exception:
                self._host_name = "localhost"

        assert self._host_name is not None
        return self._host_name

    def get_thread_name(self) -> str:
        """获取当前线程名称

        Returns:
            线程名称字符串
        """
        try:
            return threading.current_thread().name
        except Exception:
            return "MainThread"

    def get_system_info(self) -> SystemInfo:
        """获取完整的系统信息

        Returns:
            系统信息数据模型实例
        """
        return SystemInfo(
            server_name=self.get_server_name(),
            host_name=self.get_host_name(),
            thread_name=self.get_thread_name(),
        )

    def get_caller_info(self, depth: int = 2) -> CallerInfo:
        """获取调用者的类名和方法名

        Args:
            depth: 调用栈深度，默认为2，可根据实际调用层级调整

        Returns:
            调用者信息数据模型实例
        """
        try:
            frame = inspect.currentframe()
            # 向上遍历调用栈
            for _ in range(depth):
                if frame is None:
                    break
                frame = frame.f_back

            if frame is None:
                return CallerInfo(class_name=None, method_name=None)

            # 获取方法名
            method_name = frame.f_code.co_name

            # 获取类名
            class_name: str | None = None
            if "self" in frame.f_locals:
                class_name = frame.f_locals["self"].__class__.__name__
            elif "cls" in frame.f_locals:
                class_name = frame.f_locals["cls"].__name__

            return CallerInfo(class_name=class_name, method_name=method_name)

        except Exception:
            return CallerInfo(class_name=None, method_name=None)

    def get_next_seq(self) -> int:
        """获取下一个序列号（线程安全）

        使用线程锁确保在多线程环境下序列号的唯一性。

        Returns:
            递增的序列号
        """
        with self._lock:
            self._seq_counter += 1
            return self._seq_counter

    def format_datetime(self, dt: datetime.datetime) -> str:
        """格式化日期时间为Plumelog标准格式

        Args:
            dt: 日期时间对象

        Returns:
            格式化的日期时间字符串 (YYYY-MM-DD HH:MM:SS.mmm)
        """
        try:
            # 确保时间是本地时间
            if dt.tzinfo is not None:
                dt = dt.astimezone()

            # 格式化为Plumelog标准格式
            return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except Exception:
            # 如果格式化失败，使用当前时间
            now = datetime.datetime.now()
            return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def get_timestamp_ms(self, dt: datetime.datetime) -> int:
        """获取毫秒级时间戳

        Args:
            dt: 日期时间对象

        Returns:
            毫秒级时间戳
        """
        try:
            return int(dt.timestamp() * 1000)
        except Exception:
            # 如果转换失败，使用当前时间
            return int(datetime.datetime.now().timestamp() * 1000)

    def reset_sequence(self) -> None:
        """重置序列号计数器

        主要用于测试或特殊场景下的序列号重置。
        """
        with self._lock:
            self._seq_counter = 0
