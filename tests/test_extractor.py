"""测试系统信息提取器模块"""

import pytest
import threading
import datetime
from unittest.mock import patch, MagicMock
from plumelog_loguru.extractor import FieldExtractor
from plumelog_loguru.models import CallerInfo, SystemInfo


class TestFieldExtractor:
    """测试系统信息提取器"""

    def test_initialization(self):
        """测试初始化"""
        extractor = FieldExtractor()

        assert extractor._seq_counter == 0
        assert extractor._server_name is None
        assert extractor._host_name is None
        assert extractor._lock is not None

    def test_get_server_name_caching(self):
        """测试服务器名称缓存机制"""
        extractor = FieldExtractor()

        # 第一次调用
        server_name1 = extractor.get_server_name()
        assert server_name1 is not None

        # 第二次调用应该返回缓存的值
        server_name2 = extractor.get_server_name()
        assert server_name1 == server_name2
        assert extractor._server_name == server_name1

    @patch('socket.socket')
    def test_get_server_name_primary_method(self, mock_socket):
        """测试主要的IP获取方法"""
        # 模拟socket连接成功
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.return_value.__enter__.return_value = mock_sock

        extractor = FieldExtractor()
        server_name = extractor.get_server_name()

        assert server_name == "192.168.1.100"

    @patch('socket.socket')
    @patch('socket.gethostname')
    @patch('socket.gethostbyname')
    def test_get_server_name_fallback(self, mock_gethostbyname, mock_gethostname, mock_socket):
        """测试IP获取的回退机制"""
        # 第一种方法失败
        mock_socket.side_effect = Exception("Socket error")

        # 第二种方法成功
        mock_gethostname.return_value = "test-host"
        mock_gethostbyname.return_value = "192.168.1.200"

        extractor = FieldExtractor()
        server_name = extractor.get_server_name()

        assert server_name == "192.168.1.200"

    @patch('socket.socket')
    @patch('socket.gethostname')
    def test_get_server_name_final_fallback(self, mock_gethostname, mock_socket):
        """测试最终回退到127.0.0.1"""
        # 所有方法都失败
        mock_socket.side_effect = Exception("Socket error")
        mock_gethostname.side_effect = Exception("Hostname error")

        extractor = FieldExtractor()
        server_name = extractor.get_server_name()

        assert server_name == "127.0.0.1"

    def test_get_host_name(self):
        """测试主机名获取"""
        extractor = FieldExtractor()
        host_name = extractor.get_host_name()

        assert host_name is not None
        assert isinstance(host_name, str)
        assert len(host_name) > 0

    @patch('socket.gethostname')
    def test_get_host_name_fallback(self, mock_gethostname):
        """测试主机名获取失败时的回退"""
        mock_gethostname.side_effect = Exception("Hostname error")

        extractor = FieldExtractor()
        host_name = extractor.get_host_name()

        assert host_name == "localhost"

    def test_get_thread_name(self):
        """测试线程名获取"""
        extractor = FieldExtractor()
        thread_name = extractor.get_thread_name()

        # 在主线程中运行
        assert thread_name in ["MainThread", threading.current_thread().name]

    def test_get_system_info(self):
        """测试系统信息获取"""
        extractor = FieldExtractor()
        system_info = extractor.get_system_info()

        assert isinstance(system_info, SystemInfo)
        assert system_info.server_name is not None
        assert system_info.host_name is not None
        assert system_info.thread_name is not None

    def test_get_caller_info_no_frame(self):
        """测试无调用帧的情况"""
        extractor = FieldExtractor()

        # 使用很大的深度，超出调用栈
        caller_info = extractor.get_caller_info(depth=100)

        assert isinstance(caller_info, CallerInfo)
        assert caller_info.class_name is None
        assert caller_info.method_name is None

    def test_get_caller_info_with_method(self):
        """测试方法调用信息获取"""
        extractor = FieldExtractor()

        def test_function():
            # 使用depth=1来获取直接调用者的信息
            return extractor.get_caller_info(depth=1)

        caller_info = test_function()

        assert isinstance(caller_info, CallerInfo)
        assert caller_info.method_name == "test_function"

    def test_get_next_seq_thread_safety(self):
        """测试序列号生成的线程安全性"""
        extractor = FieldExtractor()
        results = []

        def generate_sequences():
            for _ in range(100):
                results.append(extractor.get_next_seq())

        # 创建多个线程同时生成序列号
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=generate_sequences)
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        # 验证序列号的唯一性
        assert len(results) == 500  # 5个线程 × 100次
        assert len(set(results)) == 500  # 所有序列号都应该是唯一的
        assert min(results) == 1
        assert max(results) == 500

    def test_format_datetime(self):
        """测试日期时间格式化"""
        extractor = FieldExtractor()

        # 测试标准datetime对象
        dt = datetime.datetime(2024, 1, 1, 12, 30, 45, 123456)
        formatted = extractor.format_datetime(dt)

        assert formatted == "2024-01-01 12:30:45.123"

    def test_format_datetime_with_timezone(self):
        """测试带时区的日期时间格式化"""
        extractor = FieldExtractor()

        # 创建带时区的datetime对象
        dt = datetime.datetime(2024, 1, 1, 12, 30, 45, 123456,
                             tzinfo=datetime.timezone.utc)
        formatted = extractor.format_datetime(dt)

        # 应该转换为本地时间并格式化
        assert isinstance(formatted, str)
        assert len(formatted) == 23  # YYYY-MM-DD HH:MM:SS.mmm

    def test_get_timestamp_ms(self):
        """测试毫秒时间戳获取"""
        extractor = FieldExtractor()

        dt = datetime.datetime(2024, 1, 1, 0, 0, 0)
        timestamp = extractor.get_timestamp_ms(dt)

        # 验证时间戳格式
        assert isinstance(timestamp, int)
        assert timestamp > 0
        # 2024年的时间戳应该是13位数字
        assert len(str(timestamp)) == 13

    def test_reset_sequence(self):
        """测试序列号重置"""
        extractor = FieldExtractor()

        # 生成一些序列号
        extractor.get_next_seq()
        extractor.get_next_seq()
        extractor.get_next_seq()

        assert extractor._seq_counter == 3

        # 重置序列号
        extractor.reset_sequence()

        assert extractor._seq_counter == 0

        # 下一个序列号应该是1
        assert extractor.get_next_seq() == 1
