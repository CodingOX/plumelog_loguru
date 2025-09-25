"""测试数据模型模块"""

import pytest
from datetime import datetime
from plumelog_loguru.models import (
    LogRecord,
    CallerInfo,
    SystemInfo,
    RedisConnectionInfo,
    BatchConfig,
)


class TestLogRecord:
    """测试LogRecord数据模型"""

    def test_create_log_record(self):
        """测试创建日志记录"""
        log_record = LogRecord(
            server_name="192.168.1.100",
            app_name="test_app",
            env="test",
            method="test_method",
            content="测试日志内容",
            log_level="INFO",
            class_name="TestClass",
            thread_name="MainThread",
            seq=1,
            date_time="2024-01-01 12:00:00.000",
            dt_time=1704067200000,
        )

        assert log_record.server_name == "192.168.1.100"
        assert log_record.app_name == "test_app"
        assert log_record.content == "测试日志内容"
        assert log_record.seq == 1

    def test_to_dict(self):
        """测试转换为字典格式"""
        log_record = LogRecord(
            server_name="192.168.1.100",
            app_name="test_app",
            env="test",
            method="test_method",
            content="测试日志内容",
            log_level="INFO",
            class_name="TestClass",
            thread_name="MainThread",
            seq=1,
            date_time="2024-01-01 12:00:00.000",
            dt_time=1704067200000,
        )

        result = log_record.to_dict()

        # 验证字典格式符合Plumelog标准
        assert result["serverName"] == "192.168.1.100"
        assert result["appName"] == "test_app"
        assert result["className"] == "TestClass"
        assert result["dateTime"] == "2024-01-01 12:00:00.000"
        assert result["dtTime"] == 1704067200000


class TestCallerInfo:
    """测试CallerInfo数据模型"""

    def test_caller_info_with_values(self):
        """测试有值的调用者信息"""
        caller_info = CallerInfo(class_name="MyClass", method_name="my_method")

        assert caller_info.class_name == "MyClass"
        assert caller_info.method_name == "my_method"
        assert caller_info.class_name_safe == "MyClass"
        assert caller_info.method_name_safe == "my_method"

    def test_caller_info_with_none_values(self):
        """测试None值的调用者信息"""
        caller_info = CallerInfo(class_name=None, method_name=None)

        assert caller_info.class_name is None
        assert caller_info.method_name is None
        assert caller_info.class_name_safe == "unknown"
        assert caller_info.method_name_safe == "unknown"


class TestSystemInfo:
    """测试SystemInfo数据模型"""

    def test_system_info(self):
        """测试系统信息创建"""
        system_info = SystemInfo(
            server_name="192.168.1.100", host_name="test-host", thread_name="MainThread"
        )

        assert system_info.server_name == "192.168.1.100"
        assert system_info.host_name == "test-host"
        assert system_info.thread_name == "MainThread"


class TestRedisConnectionInfo:
    """测试Redis连接信息模型"""

    def test_redis_connection_info(self):
        """测试Redis连接信息创建"""
        redis_info = RedisConnectionInfo(
            host="localhost", port=6379, db=0, password="secret", max_connections=10
        )

        assert redis_info.host == "localhost"
        assert redis_info.port == 6379
        assert redis_info.db == 0
        assert redis_info.password == "secret"
        assert redis_info.max_connections == 10

    def test_redis_connection_info_validation(self):
        """测试Redis连接信息验证"""
        # 测试端口范围验证
        with pytest.raises(ValueError):
            RedisConnectionInfo(
                host="localhost",
                port=70000,  # 超出端口范围
                db=0,
            )  # type: ignore

        # 测试数据库编号验证
        with pytest.raises(ValueError):
            RedisConnectionInfo(
                host="localhost",
                port=6379,
                db=-1,  # 负数数据库编号
            )  # type: ignore


class TestBatchConfig:
    """测试批处理配置模型"""

    def test_batch_config(self):
        """测试批处理配置创建"""
        batch_config = BatchConfig(
            batch_size=50, batch_interval_seconds=1.5, queue_max_size=5000
        )

        assert batch_config.batch_size == 50
        assert batch_config.batch_interval_seconds == 1.5
        assert batch_config.queue_max_size == 5000

    def test_batch_config_validation(self):
        """测试批处理配置验证"""
        # 测试批量大小验证
        with pytest.raises(ValueError):
            BatchConfig(
                batch_size=0,  # 批量大小不能为0
                batch_interval_seconds=1.0,
                queue_max_size=1000,
            )

        # 测试间隔时间验证
        with pytest.raises(ValueError):
            BatchConfig(
                batch_size=100,
                batch_interval_seconds=0,  # 间隔时间必须大于0
                queue_max_size=1000,
            )
