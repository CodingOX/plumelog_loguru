"""数据模型模块

定义Plumelog系统中使用的所有数据实体类。

设计决策：
- LogRecord/CallerInfo/SystemInfo：高频创建的内部传递类，使用 dataclass(slots=True)
  以大幅降低实例化时的 CPU 和内存开销（相比 Pydantic BaseModel 低 1-2 个数量级）。
- RedisConnectionInfo/BatchConfig：配置类，保留 Pydantic 以利用字段范围校验能力。
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass(slots=True)
class LogRecord:
    """Plumelog日志记录数据模型（高频创建，使用 dataclass+slots 降低开销）

    slots=True 避免每个实例的 __dict__，进一步减少内存占用。
    接口与原 Pydantic 版本保持兼容（关键字参数构造 + to_dict()）。
    """

    server_name: str
    app_name: str
    env: str
    method: str
    content: str
    log_level: str
    class_name: str
    thread_name: str
    seq: int
    date_time: str
    dt_time: int

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于JSON序列化

        Returns:
            包含所有字段的字典（key 为 Plumelog 标准 camelCase 格式）
        """
        return {
            "serverName": self.server_name,
            "appName": self.app_name,
            "env": self.env,
            "method": self.method,
            "content": self.content,
            "logLevel": self.log_level,
            "className": self.class_name,
            "threadName": self.thread_name,
            "seq": self.seq,
            "dateTime": self.date_time,
            "dtTime": self.dt_time,
        }


@dataclass(frozen=True, slots=True)
class CallerInfo:
    """调用者信息数据模型（内部传递用，frozen dataclass）

    frozen=True 保证不可变性，与原 Pydantic frozen 版本行为一致。
    """

    class_name: str | None
    method_name: str | None

    @property
    def class_name_safe(self) -> str:
        """获取安全的类名，如果为None则返回默认值"""
        return self.class_name or "unknown"

    @property
    def method_name_safe(self) -> str:
        """获取安全的方法名，如果为None则返回默认值"""
        return self.method_name or "unknown"


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """系统信息数据模型（内部传递用，frozen dataclass）"""

    server_name: str
    host_name: str
    thread_name: str


class RedisConnectionInfo(BaseModel):
    """Redis连接信息数据模型（配置类，保留 Pydantic 字段校验）

    封装Redis连接的所有必要参数。
    """

    model_config = ConfigDict(frozen=True)

    host: str = Field(..., description="Redis主机地址")
    port: int = Field(..., ge=1, le=65535, description="Redis端口")
    db: int = Field(..., ge=0, description="Redis数据库编号")
    password: str | None = Field(None, description="Redis密码")
    max_connections: int = Field(5, ge=1, description="最大连接数")


class BatchConfig(BaseModel):
    """批处理配置数据模型（配置类，保留 Pydantic 字段校验）

    封装日志批量发送的配置参数。
    """

    model_config = ConfigDict(frozen=True)

    batch_size: int = Field(100, ge=1, description="批量发送大小")
    batch_interval_seconds: float = Field(2.0, gt=0, description="批量发送间隔时间")
    queue_max_size: int = Field(10000, ge=1, description="队列最大大小")
