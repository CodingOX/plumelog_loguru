"""数据模型模块

定义Plumelog系统中使用的所有数据实体类，替代字典作为数据交互载体。
所有模型都包含完整的类型提示和验证逻辑。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict


class LogRecord(BaseModel):
    """Plumelog日志记录数据模型

    表示一条完整的日志记录，包含所有必要的字段信息。
    用于替代原有的字典结构，提供强类型保证。
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=False,
    )

    server_name: str = Field(..., description="服务器名称或IP地址")
    app_name: str = Field(..., description="应用名称")
    env: str = Field(..., description="运行环境")
    method: str = Field(..., description="调用方法名")
    content: str = Field(..., description="日志内容")
    log_level: str = Field(..., description="日志级别")
    class_name: str = Field(..., description="调用类名")
    thread_name: str = Field(..., description="线程名称")
    seq: int = Field(..., ge=0, description="序列号")
    date_time: str = Field(..., description="格式化的日期时间字符串")
    dt_time: int = Field(..., ge=0, description="Unix时间戳（毫秒）")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于JSON序列化

        Returns:
            包含所有字段的字典
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


class CallerInfo(BaseModel):
    """调用者信息数据模型

    封装从调用栈中提取的类名和方法名信息。
    """

    model_config = ConfigDict(frozen=True)

    class_name: Optional[str] = Field(None, description="调用者类名")
    method_name: Optional[str] = Field(None, description="调用者方法名")

    @property
    def class_name_safe(self) -> str:
        """获取安全的类名，如果为None则返回默认值"""
        return self.class_name or "unknown"

    @property
    def method_name_safe(self) -> str:
        """获取安全的方法名，如果为None则返回默认值"""
        return self.method_name or "unknown"


class SystemInfo(BaseModel):
    """系统信息数据模型

    封装服务器和主机相关的系统信息。
    """

    model_config = ConfigDict(frozen=True)

    server_name: str = Field(..., description="服务器IP地址")
    host_name: str = Field(..., description="主机名")
    thread_name: str = Field(..., description="当前线程名")


class RedisConnectionInfo(BaseModel):
    """Redis连接信息数据模型

    封装Redis连接的所有必要参数。
    """

    model_config = ConfigDict(frozen=True)

    host: str = Field(..., description="Redis主机地址")
    port: int = Field(..., ge=1, le=65535, description="Redis端口")
    db: int = Field(..., ge=0, description="Redis数据库编号")
    password: Optional[str] = Field(None, description="Redis密码")
    max_connections: int = Field(5, ge=1, description="最大连接数")


class BatchConfig(BaseModel):
    """批处理配置数据模型

    封装日志批量发送的配置参数。
    """

    model_config = ConfigDict(frozen=True)

    batch_size: int = Field(100, ge=1, description="批量发送大小")
    batch_interval_seconds: float = Field(2.0, gt=0, description="批量发送间隔时间")
    queue_max_size: int = Field(10000, ge=1, description="队列最大大小")
