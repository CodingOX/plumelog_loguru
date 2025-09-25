"""测试配置管理模块"""

import os
import pytest
from plumelog_loguru.config import PlumelogSettings


class TestPlumelogSettings:
    """测试Plumelog配置类"""

    def test_default_settings(self):
        """测试默认配置"""
        settings = PlumelogSettings()

        assert settings.app_name == "default"
        assert settings.env == "dev"
        assert settings.redis_host == "localhost"
        assert settings.redis_port == 6379
        assert settings.redis_db == 0
        assert settings.redis_password is None
        assert settings.batch_size == 100
        assert settings.batch_interval_seconds == 2.0
        assert settings.retry_count == 3

    def test_custom_settings(self):
        """测试自定义配置"""
        settings = PlumelogSettings(
            app_name="test_app",
            env="production",
            redis_host="redis.example.com",
            redis_port=6380,
            redis_password="secret123",
            batch_size=50,
        )

        assert settings.app_name == "test_app"
        assert settings.env == "production"
        assert settings.redis_host == "redis.example.com"
        assert settings.redis_port == 6380
        assert settings.redis_password == "secret123"
        assert settings.batch_size == 50

    def test_env_variable_override(self, monkeypatch):
        """测试环境变量覆盖配置"""
        # 设置环境变量
        monkeypatch.setenv("PLUMELOG_APP_NAME", "env_app")
        monkeypatch.setenv("PLUMELOG_ENV", "staging")
        monkeypatch.setenv("PLUMELOG_REDIS_HOST", "env.redis.com")
        monkeypatch.setenv("PLUMELOG_REDIS_PORT", "6380")
        monkeypatch.setenv("PLUMELOG_BATCH_SIZE", "200")

        settings = PlumelogSettings()

        assert settings.app_name == "env_app"
        assert settings.env == "staging"
        assert settings.redis_host == "env.redis.com"
        assert settings.redis_port == 6380
        assert settings.batch_size == 200

    def test_redis_connection_info(self):
        """测试Redis连接信息属性"""
        settings = PlumelogSettings(
            redis_host="test.redis.com",
            redis_port=6380,
            redis_db=2,
            redis_password="test123",
            max_connections=10,
        )

        redis_info = settings.redis_connection_info

        assert redis_info.host == "test.redis.com"
        assert redis_info.port == 6380
        assert redis_info.db == 2
        assert redis_info.password == "test123"
        assert redis_info.max_connections == 10

    def test_batch_config(self):
        """测试批处理配置属性"""
        settings = PlumelogSettings(
            batch_size=75, batch_interval_seconds=1.5, queue_max_size=5000
        )

        batch_config = settings.batch_config

        assert batch_config.batch_size == 75
        assert batch_config.batch_interval_seconds == 1.5
        assert batch_config.queue_max_size == 5000

    def test_get_redis_url_with_password(self):
        """测试带密码的Redis URL生成"""
        settings = PlumelogSettings(
            redis_host="redis.example.com",
            redis_port=6379,
            redis_db=1,
            redis_password="secret123",
        )

        url = settings.get_redis_url()
        expected = "redis://:secret123@redis.example.com:6379/1"

        assert url == expected

    def test_get_redis_url_without_password(self):
        """测试不带密码的Redis URL生成"""
        settings = PlumelogSettings(
            redis_host="localhost", redis_port=6379, redis_db=0, redis_password=None
        )

        url = settings.get_redis_url()
        expected = "redis://localhost:6379/0"

        assert url == expected

    def test_validation_errors(self):
        """测试配置验证错误"""
        # 测试无效的端口号
        with pytest.raises(ValueError):
            PlumelogSettings(redis_port=70000)

        # 测试无效的数据库编号
        with pytest.raises(ValueError):
            PlumelogSettings(redis_db=-1)

        # 测试无效的批量大小
        with pytest.raises(ValueError):
            PlumelogSettings(batch_size=0)

        # 测试无效的间隔时间
        with pytest.raises(ValueError):
            PlumelogSettings(batch_interval_seconds=0)
