"""配置管理模块

提供Plumelog的配置管理功能，使用Pydantic进行配置验证和类型检查。
支持从环境变量读取配置，并提供合理的默认值。
"""

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import RedisConnectionInfo, BatchConfig


class PlumelogSettings(BaseSettings):
    """Plumelog配置类

    负责从环境变量获取配置参数并提供默认值。
    使用Pydantic进行配置验证，确保配置参数的正确性。
    """

    model_config = SettingsConfigDict(
        env_prefix="plumelog_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用相关配置
    app_name: str = Field(default="default", description="应用名称")
    env: str = Field(default="dev", description="运行环境")

    # Redis连接配置
    redis_host: str = Field(default="localhost", description="Redis主机地址")
    redis_port: int = Field(default=6379, ge=1, le=65535, description="Redis端口")
    redis_db: int = Field(default=0, ge=0, description="Redis数据库编号")
    redis_password: str | None = Field(default=None, description="Redis密码")
    redis_key: str = Field(default="plume_log_list", description="Redis队列键名")
    max_connections: int = Field(default=5, ge=1, description="Redis最大连接数")

    # 批处理配置
    queue_max_size: int = Field(default=10000, ge=1, description="内存队列最大大小")
    batch_size: int = Field(default=100, ge=1, description="批量发送大小")
    batch_interval_seconds: int = Field(
        default=2, gt=0, description="批量发送间隔时间（秒）"
    )

    # 重试配置
    retry_count: int = Field(default=3, ge=1, description="Redis发送重试次数")
    retry_delay: int = Field(default=2, gt=0, description="重试延迟时间（秒）")

    # 连接超时配置
    socket_connect_timeout: int = Field(
        default=5, gt=0, description="Socket连接超时时间（秒）"
    )
    socket_timeout: int = Field(default=5.0, gt=0, description="Socket超时时间（秒）")

    # 临时缓存配置
    temp_buffer_max_size: int = Field(
        default=1000, ge=1, description="临时缓存最大容量"
    )

    @computed_field
    @property
    def redis_connection_info(self) -> RedisConnectionInfo:
        """获取Redis连接信息对象"""
        return RedisConnectionInfo(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            password=self.redis_password,
            max_connections=self.max_connections,
        )

    @computed_field
    @property
    def batch_config(self) -> BatchConfig:
        """获取批处理配置对象"""
        return BatchConfig(
            batch_size=self.batch_size,
            batch_interval_seconds=self.batch_interval_seconds,
            queue_max_size=self.queue_max_size,
        )

    def get_redis_url(self) -> str:
        """构建Redis连接URL

        Returns:
            Redis连接URL字符串
        """
        if self.redis_password:
            return (
                f"redis://:{self.redis_password}@{self.redis_host}:"
                f"{self.redis_port}/{self.redis_db}"
            )
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
