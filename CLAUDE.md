## 项目概述

`plumelog-loguru` 是一个 Python 库，为 Loguru 提供与 Plumelog 系统的集成，通过异步 Redis 传输日志。核心采用**生产者-消费者模型**，业务线程（生产者）通过线程安全队列将日志委托给专用事件循环线程（消费者），实现非阻塞日志投递。

## 常用命令

```bash
uv sync --dev              # 安装开发依赖
uv run pytest              # 运行全部测试
uv run pytest tests/test_config.py -v   # 运行单个测试文件
uv run pytest -k "test_name" -v         # 按名称筛选测试
uv run ruff format .       # 代码格式化（替代 black + isort）
uv run ruff check --fix .  # 自动修复可修复的 lint 问题
uv run ruff check .        # 仅检查，不修改文件（CI 同款）
uv run mypy src/           # 类型检查
uv run python -m build     # 构建 wheel
uv run twine check dist/*  # 检查构建产物
```

测试配置（pyproject.toml）已启用 `asyncio_mode = "auto"` 并默认生成覆盖率报告。

## 核心架构

### 模块职责

| 模块 | 职责 |
|------|------|
| `config.py` | Pydantic Settings，支持 `PLUMELOG_` 前缀环境变量和 `.env` 文件 |
| `models.py` | 数据实体：`LogRecord`、`CallerInfo`、`SystemInfo`、`RedisConnectionInfo`、`BatchConfig` |
| `extractor.py` | 系统信息提取（IP、主机名、线程名、调用栈），线程安全序列号 |
| `redis_client.py` | 异步 Redis 客户端，连接池管理，指数退避重试 |
| `redis_sink.py` | Loguru sink 实现，核心编排层 |

### RedisSink 关键设计

1. **专用事件循环线程**：`_AsyncRuntime` 类按需启动守护线程托管独立 `asyncio` 事件循环，通过 `asyncio.run_coroutine_threadsafe` 完成跨线程调度。使用 `threading.Event` 确保线程就绪后才返回。

2. **双层缓冲**：
   - 主队列 `asyncio.Queue`（有界，`queue_max_size`）
   - 线程安全临时缓存 `deque`（有界，`temp_buffer_max_size`），用于初始化前或队列满时的降级存储

3. **背压控制**：`_pending_submit_semaphore`（`threading.BoundedSemaphore`）限制在途任务数 = `queue_max_size + temp_buffer_max_size`，超出则写入临时缓存并告警。

4. **延迟初始化**：`__init__` 不启动事件循环和 Redis 连接，首次 `__call__` 时通过 `_ensure_initialized` 按需初始化。`async with RedisSink(config)` 会提前初始化消费者。

5. **优雅关闭**：`close()` → `_closing=True`（拒绝新提交）→ 等待队列排空（10s 超时）→ 等待消费者完成（10s 超时）→ 发送临时缓存 → 发送队列残留 → 断开 Redis → 停止事件循环。

6. **容错**：Redis 发送失败由 `AsyncRedisClient` 执行有限次指数退避重试，耗尽后丢弃批次并打印计数，不阻塞关闭流程。

### 数据流

```
logger.info() → RedisSink.__call__() [同步线程]
  → _convert_to_log_record() [本线程转换]
  → _ensure_runtime().submit(_async_handle_log) [跨线程调度]
  → _ensure_initialized() → Queue.put()
  → _log_consumer() [后台协程]
    → 按 batch_size 或 batch_interval_seconds 聚合
    → AsyncRedisClient.send_log_records() [Pipeline LPUSH]
```

### 测试结构

- `tests/conftest.py`：提供 `test_config`（测试用 PlumelogSettings）、`mock_redis`（AsyncMock）、`event_loop` fixtures
- 使用 `unittest.mock.AsyncMock` 模拟 Redis，无需真实 Redis 即可运行

## 配置约定

- 所有 `PLUMELOG_` 前缀的环境变量自动映射到 `PlumelogSettings` 字段
- `get_redis_url()` 返回完整 Redis URL（含密码 URL 编码）
- `redis_connection_info` 和 `batch_config` 属性提供只读分组视图
