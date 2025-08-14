# Plumelog-Loguru

一个现代化的 Python 库，为 Loguru 提供与 Plumelog 系统的集成功能，支持异步 Redis 日志传输。

## ✨ 特性

- 🚀 **异步处理**: 基于 asyncio 的高性能异步日志传输
- 📦 **批量优化**: 智能批量处理，减少 Redis 连接开销
- 🔒 **类型安全**: 完整的 Python 3.10+ 类型提示
- 🔄 **智能重试**: 指数退避重试机制，确保日志不丢失
- 🏊 **连接池**: Redis 连接池管理，提高并发性能
- ⚙️ **灵活配置**: 基于 Pydantic 的配置管理，支持环境变量
- 🧵 **线程安全**: 多线程环境下的安全操作

## 📦 安装

使用 uv 安装（推荐）：

```bash
uv add plumelog-loguru
```

使用 pip 安装：

```bash
pip install plumelog-loguru
```

## 🚀 快速开始

### 基本使用

```python
from loguru import logger
from plumelog_loguru import create_redis_sink

# 使用默认配置添加 Redis sink
logger.add(create_redis_sink())  # type: ignore[arg-type]

# 开始记录日志
logger.info("Hello, Plumelog!")
logger.error("这是一个错误日志")
```

### 自定义配置

```python
from loguru import logger
from plumelog_loguru import create_redis_sink, PlumelogSettings

# 创建自定义配置
config = PlumelogSettings(
    app_name="my_application",
    env="production",
    redis_host="redis.example.com",
    redis_port=6379,
    redis_password="your_password",
    batch_size=50,
    batch_interval_seconds=1.0
)

# 使用自定义配置
logger.add(create_redis_sink(config))  # type: ignore[arg-type]
```

### 环境变量配置

支持通过环境变量进行配置，所有配置项都支持 `PLUMELOG_` 前缀：

```bash
export PLUMELOG_APP_NAME=my_app
export PLUMELOG_ENV=production
export PLUMELOG_REDIS_HOST=localhost
export PLUMELOG_REDIS_PORT=6379
export PLUMELOG_REDIS_PASSWORD=secret
export PLUMELOG_BATCH_SIZE=100
```

### 异步上下文使用

```python
import asyncio
from loguru import logger
from plumelog_loguru import RedisSink, PlumelogSettings

async def main():
    config = PlumelogSettings(app_name="async_app")
    
    async with RedisSink(config) as sink:
        logger.add(sink)  # type: ignore[arg-type]
        logger.info("异步环境中的日志")
        await asyncio.sleep(1)

asyncio.run(main())
```

## ⚙️ 配置选项

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| `app_name` | `PLUMELOG_APP_NAME` | `"default"` | 应用名称 |
| `env` | `PLUMELOG_ENV` | `"dev"` | 运行环境 |
| `redis_host` | `PLUMELOG_REDIS_HOST` | `"localhost"` | Redis 主机地址 |
| `redis_port` | `PLUMELOG_REDIS_PORT` | `6379` | Redis 端口 |
| `redis_db` | `PLUMELOG_REDIS_DB` | `0` | Redis 数据库编号 |
| `redis_password` | `PLUMELOG_REDIS_PASSWORD` | `None` | Redis 密码 |
| `redis_key` | `PLUMELOG_REDIS_KEY` | `"plume_log_list"` | Redis 队列键名 |
| `batch_size` | `PLUMELOG_BATCH_SIZE` | `100` | 批量发送大小 |
| `batch_interval_seconds` | `PLUMELOG_BATCH_INTERVAL_SECONDS` | `2.0` | 批量发送间隔（秒） |
| `queue_max_size` | `PLUMELOG_QUEUE_MAX_SIZE` | `10000` | 内存队列最大大小 |
| `retry_count` | `PLUMELOG_RETRY_COUNT` | `3` | 重试次数 |
| `max_connections` | `PLUMELOG_MAX_connections` | `5` | Redis 最大连接数 |

## 🔍 类型检查与最佳实践

### Pylance/mypy 类型检查说明

由于 Loguru 的 `logger.add()` 方法对 sink 参数有严格的类型要求，您可能会遇到以下类型检查警告：

```text
"add"的重载与提供的参数不匹配
无法将"RedisSink"类型的参数分配给函数"add"中类型为"str | PathLikeStr"的参数"sink"
```

### 推荐解决方案

#### 方案1：使用类型忽略注释（推荐）

```python
from loguru import logger
from plumelog_loguru import create_redis_sink

# 推荐做法：添加类型忽略注释
logger.add(create_redis_sink())  # type: ignore[arg-type]
```

#### 方案2：使用工厂函数（最佳实践）

```python
# 始终使用 create_redis_sink() 工厂函数，而不是直接实例化 RedisSink
sink = create_redis_sink(config)
logger.add(sink)  # type: ignore[arg-type]

# 避免直接使用 RedisSink 类
# sink = RedisSink(config)  # 不推荐
```

### 为什么需要类型忽略？

1. **运行时正常**：代码在运行时完全正常工作，因为 `RedisSink` 实现了 `__call__` 方法
2. **静态检查限制**：Pylance 无法自动识别 `RedisSink` 符合 `Callable[[Record], None]` 协议
3. **设计权衡**：这是类型安全与 API 灵活性之间的合理权衡

### 完整示例

```python
import sys
from loguru import logger
from plumelog_loguru import create_redis_sink, PlumelogSettings

def setup_logging():
    """设置日志系统的推荐方式"""
    # 移除默认处理器（可选）
    logger.remove()
    
    # 添加控制台输出
    logger.add(sys.stderr, level="INFO")
    
    # 添加 Redis sink
    config = PlumelogSettings(
        app_name="my_app",
        env="production"
    )
    redis_sink = create_redis_sink(config)
    logger.add(redis_sink, level="DEBUG")  # type: ignore[arg-type]
    
    return logger

# 在应用启动时调用
setup_logging()
logger.info("日志系统已初始化")
```

## 🏗️ 架构设计

本库采用现代 Python 设计模式：

- **数据模型**: 使用 Pydantic 数据类替代字典，提供类型安全
- **异步优先**: 基于 asyncio 的非阻塞设计
- **组件解耦**: 清晰的模块边界和依赖注入
- **错误处理**: 全面的异常处理和降级策略

## 🔧 开发

### 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd plumelog-loguru

# 安装开发依赖
uv sync --all-extras

# 运行测试
uv run pytest

# 代码格式化
uv run black src tests
uv run isort src tests

# 类型检查
uv run mypy src
```

### 项目结构

```text
src/plumelog_loguru/
├── __init__.py          # 主要 API 导出
├── config.py            # 配置管理
├── models.py            # 数据模型定义
├── extractor.py         # 系统信息提取器
├── redis_client.py      # 异步 Redis 客户端
└── redis_sink.py        # Loguru Redis Sink 实现
```

## 📝 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
