# Redis Sink Runtime Reliability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 RedisSink 在高压、Redis 失败和关闭阶段的性能、资源泄露、数据丢失与契约漂移问题。

**Architecture:** 先用测试锁定失败路径，再把 RedisSink 改成有明确背压、有限失败处理和可终止关闭流程的运行时。RedisClient 保持小接口，但修正超时连接清理和失败返回处理；文档同步为“尽力发送”语义，避免继续承诺绝对不丢失。

**Tech Stack:** Python 3.10+、asyncio、redis.asyncio、Loguru、Pydantic、pytest、ruff、mypy、uv。

---

## File Structure

- Modify: `src/plumelog_loguru/redis_sink.py`
  - 调整后台 runtime 延迟启动。
  - 修复队列背压和失败重排逻辑。
  - 修复 close 阶段无限等待和 pending task 泄露。
  - 优先使用 Loguru record 字段，减少 inspect 热路径调用。
- Modify: `src/plumelog_loguru/redis_client.py`
  - 将 redis `TimeoutError` 纳入连接清理。
  - 保持 `send_log_records()` 返回 bool，但调用方必须显式处理失败。
- Modify: `src/plumelog_loguru/extractor.py`
  - 如果仍保留 `get_caller_info()`，用 `finally: del frame` 释放 frame 引用。
- Modify: `src/plumelog_loguru/config.py`
  - 修复 `get_redis_url()` 的密码 URL 编码。
- Modify: `src/plumelog_loguru/__init__.py`
  - 同步运行时版本与包版本，修正作者元数据。
- Modify: `README.md`
  - 修正“不丢失”“零成本”“队列满丢弃”等不准确表述。
  - 修正类图字段/方法名和配置表。
- Create/Modify: `tests/test_redis_sink.py`
  - 补失败重排、close 超时、背压行为、延迟启动 runtime 测试。
- Create: `tests/test_redis_client.py`
  - 补连接成功/失败、Timeout 清理、批量发送失败返回测试。
- Modify: `tests/test_config.py`
  - 补 Redis URL 密码保留字符编码测试。

---

## Task 1: Lock RedisSink Failure Semantics With Tests

**Files:**
- Modify: `tests/test_redis_sink.py`

- [ ] **Step 1: Add failing client test doubles**

Add these helpers near the existing `DummyAsyncRedisClient`:

```python
class AlwaysFailAsyncRedisClient:
    """测试替身：模拟 Redis 持续不可用"""

    def __init__(self, config) -> None:  # noqa: D401, ANN001
        self.config = config
        self.send_calls = 0
        self.disconnect_calls = 0

    async def send_log_records(
        self, records: list[LogRecord], key: str | None = None
    ) -> bool:
        self.send_calls += 1
        return False

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
```

- [ ] **Step 2: Add close-must-return failure test**

Add this test:

```python
def test_redis_sink_close_returns_when_redis_send_keeps_failing(
    monkeypatch, test_config
) -> None:
    """Redis 持续失败时 close 不能卡死在 queue.join()"""
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", AlwaysFailAsyncRedisClient
    )
    test_config.queue_max_size = 2
    test_config.batch_size = 1
    test_config.batch_interval_seconds = 0.01
    sink = RedisSink(test_config)

    sink(_build_message("will-fail"))  # type: ignore[arg-type]

    async def close_with_timeout() -> None:
        await asyncio.sleep(0.05)
        await asyncio.wait_for(sink.close(), timeout=1.0)

    asyncio.run(close_with_timeout())

    client = cast(AlwaysFailAsyncRedisClient, sink.redis_client)
    assert client.disconnect_calls == 1
```

- [ ] **Step 3: Run targeted test and verify it fails on current code**

Run:

```bash
uv run pytest tests/test_redis_sink.py::test_redis_sink_close_returns_when_redis_send_keeps_failing -q
```

Expected before implementation:

```text
FAILED ... TimeoutError
```

---

## Task 2: Fix RedisSink Close And Failure Requeue Loop

**Files:**
- Modify: `src/plumelog_loguru/redis_sink.py`
- Test: `tests/test_redis_sink.py`

- [ ] **Step 1: Stop requeueing failed batches during normal consumer loop**

Replace the failure branch in `_log_consumer()`:

```python
if success:
    for _ in batch:
        self._log_queue.task_done()
else:
    for _ in batch:
        self._log_queue.task_done()
    print(f"[RedisSink] Redis发送最终失败，丢弃 {len(batch)} 条日志")
```

Do not put the same batch back into `_log_queue`. `AsyncRedisClient` already owns retry and backoff.

- [ ] **Step 2: Make close bounded**

Change `_async_close()` so it does not wait forever:

```python
self._running = False

if self._log_queue:
    try:
        await asyncio.wait_for(self._log_queue.join(), timeout=10.0)
    except asyncio.TimeoutError:
        print("[RedisSink] 等待队列清空超时，将继续关闭并清理剩余日志")
```

Keep the existing consumer cancellation block after this.

- [ ] **Step 3: Handle final flush failure explicitly**

In `_flush_temp_buffer_to_redis()`:

```python
success = await self.redis_client.send_log_records(buffered_logs)
if not success:
    print(f"[RedisSink] 临时缓存发送失败，丢弃 {len(buffered_logs)} 条日志")
```

In the remaining queue flush block:

```python
success = await self.redis_client.send_log_records(remaining_logs)
if not success:
    print(f"[RedisSink] 剩余队列日志发送失败，丢弃 {len(remaining_logs)} 条日志")
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_redis_sink.py -q
```

Expected:

```text
3 passed
```

The exact count may be higher if more tests are added in this task.

---

## Task 3: Add Real Backpressure Instead Of Unbounded Pending Futures

**Files:**
- Modify: `src/plumelog_loguru/redis_sink.py`
- Modify: `tests/test_redis_sink.py`

- [ ] **Step 1: Add a bounded submit semaphore**

In `RedisSink.__init__()` add:

```python
self._pending_submit_limit = self.config.queue_max_size + self.config.temp_buffer_max_size
self._pending_submit_semaphore = threading.BoundedSemaphore(
    self._pending_submit_limit
)
```

- [ ] **Step 2: Refuse new background tasks when pressure limit is reached**

In `__call__()` before `_runtime.submit(...)`:

```python
if not self._pending_submit_semaphore.acquire(blocking=False):
    print("[RedisSink] 后台任务积压过高，日志写入临时缓存")
    self._store_to_temp_buffer(log_record)
    return
```

After future completion, release the semaphore:

```python
future = self._runtime.submit(self._async_handle_log(log_record))

def _on_done(done_future: Future[Any]) -> None:
    self._pending_submit_semaphore.release()
    self._log_future_exception(done_future)

future.add_done_callback(_on_done)
```

- [ ] **Step 3: Add pressure test**

Add a test that fills the semaphore and verifies new logs go to temp buffer instead of creating unbounded tasks:

```python
def test_redis_sink_uses_temp_buffer_when_pending_submit_limit_is_reached(
    monkeypatch, test_config
) -> None:
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    test_config.queue_max_size = 1
    test_config.temp_buffer_max_size = 2
    sink = RedisSink(test_config)

    for _ in range(sink._pending_submit_limit):
        assert sink._pending_submit_semaphore.acquire(blocking=False)

    sink(_build_message("overflow"))  # type: ignore[arg-type]

    assert len(sink._temp_buffer) == 1

    for _ in range(sink._pending_submit_limit):
        sink._pending_submit_semaphore.release()
    asyncio.run(sink.close())
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_redis_sink.py -q
```

Expected:

```text
passed
```

---

## Task 4: Delay Runtime Startup Until First Use

**Files:**
- Modify: `src/plumelog_loguru/redis_sink.py`
- Modify: `tests/test_redis_sink.py`

- [ ] **Step 1: Add runtime accessor**

In `RedisSink`:

```python
def _ensure_runtime(self) -> _AsyncRuntime:
    """按需启动专用事件循环，避免构造未使用 sink 时占用线程。"""
    if self._runtime is None:
        self._runtime = _AsyncRuntime()
    return self._runtime
```

- [ ] **Step 2: Stop starting runtime in `__init__()`**

Change:

```python
self._runtime: _AsyncRuntime | None = _AsyncRuntime()
```

to:

```python
self._runtime: _AsyncRuntime | None = None
```

- [ ] **Step 3: Use accessor in submit paths**

In `__call__()`:

```python
runtime = self._ensure_runtime()
future = runtime.submit(self._async_handle_log(log_record))
```

In `_run_in_runtime()`:

```python
future = self._ensure_runtime().submit(coro)
return await asyncio.wrap_future(future)
```

In `_async_close()`, keep the early return only if runtime was never created:

```python
if not self._runtime:
    return
```

- [ ] **Step 4: Add lazy startup test**

```python
def test_redis_sink_does_not_start_runtime_until_first_use(test_config) -> None:
    sink = RedisSink(test_config)

    assert sink._runtime is None

    asyncio.run(sink.close())
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run pytest tests/test_redis_sink.py -q
```

Expected:

```text
passed
```

---

## Task 5: Reduce Inspect Hot Path And Release Frames Safely

**Files:**
- Modify: `src/plumelog_loguru/redis_sink.py`
- Modify: `src/plumelog_loguru/extractor.py`
- Modify: `tests/test_redis_sink.py`
- Modify: `tests/test_extractor.py`

- [ ] **Step 1: Prefer Loguru record fields in `_convert_to_log_record()`**

Replace caller extraction in `_convert_to_log_record()`:

```python
record_dict = getattr(message, "record", {})
caller_info = self.field_extractor.get_caller_info(depth=3)
method = str(record_dict.get("function") or caller_info.method_name_safe)
class_name = str(record_dict.get("name") or caller_info.class_name_safe)
```

Then use `method=method` and `class_name=class_name` when building `LogRecord`.

- [ ] **Step 2: Move `import datetime` to module top**

At top of `redis_sink.py` add:

```python
import datetime
```

Remove the function-local `import datetime`.

- [ ] **Step 3: Release frame reference in extractor**

Change `get_caller_info()`:

```python
frame = None
try:
    frame = inspect.currentframe()
    for _ in range(depth):
        if frame is None:
            break
        frame = frame.f_back

    if frame is None:
        return CallerInfo(class_name=None, method_name=None)

    method_name = frame.f_code.co_name
    class_name: str | None = None
    if "self" in frame.f_locals:
        class_name = frame.f_locals["self"].__class__.__name__
    elif "cls" in frame.f_locals:
        class_name = frame.f_locals["cls"].__name__

    return CallerInfo(class_name=class_name, method_name=method_name)
except Exception:
    return CallerInfo(class_name=None, method_name=None)
finally:
    del frame
```

- [ ] **Step 4: Add conversion test for Loguru fields**

```python
def test_redis_sink_uses_loguru_record_caller_fields(monkeypatch, test_config) -> None:
    monkeypatch.setattr(
        "plumelog_loguru.redis_sink.AsyncRedisClient", DummyAsyncRedisClient
    )
    sink = RedisSink(test_config)
    message = SimpleNamespace(
        record={
            "message": "caller",
            "level": SimpleNamespace(name="INFO"),
            "time": datetime.datetime.now(),
            "function": "handler",
            "name": "app.service",
        }
    )

    record = sink._convert_to_log_record(message)  # type: ignore[arg-type]

    assert record.method == "handler"
    assert record.class_name == "app.service"
    asyncio.run(sink.close())
```

- [ ] **Step 5: Run extractor and sink tests**

Run:

```bash
uv run pytest tests/test_extractor.py tests/test_redis_sink.py -q
```

Expected:

```text
passed
```

---

## Task 6: Fix RedisClient Timeout Handling And URL Encoding

**Files:**
- Modify: `src/plumelog_loguru/redis_client.py`
- Modify: `src/plumelog_loguru/config.py`
- Create: `tests/test_redis_client.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add Redis timeout cleanup test**

Create `tests/test_redis_client.py`:

```python
import pytest
from redis.exceptions import TimeoutError

from plumelog_loguru.redis_client import AsyncRedisClient


@pytest.mark.asyncio
async def test_handle_send_error_cleans_up_timeout_error(monkeypatch, test_config):
    client = AsyncRedisClient(test_config)
    client._connected = True
    cleanup_called = False

    async def fake_cleanup() -> None:
        nonlocal cleanup_called
        cleanup_called = True
        client._connected = False

    monkeypatch.setattr(client, "_cleanup_on_error", fake_cleanup)

    await client._handle_send_error(TimeoutError("timeout"), attempt=0, log_count=1)

    assert cleanup_called is True
    assert client._connected is False
```

- [ ] **Step 2: Import Redis TimeoutError**

In `redis_client.py`:

```python
from redis.exceptions import ConnectionError, RedisError, TimeoutError
```

- [ ] **Step 3: Include timeout in cleanup condition**

```python
if isinstance(error, (ConnectionError, TimeoutError, OSError)):
    await self._cleanup_on_error()
```

- [ ] **Step 4: Add URL password encoding test**

In `tests/test_config.py`:

```python
def test_get_redis_url_encodes_password_reserved_characters(self) -> None:
    settings = PlumelogSettings(
        redis_host="redis.example.com",
        redis_port=6379,
        redis_db=1,
        redis_password="a@b:c/d#e",
    )

    assert settings.get_redis_url() == "redis://:a%40b%3Ac%2Fd%23e@redis.example.com:6379/1"
```

- [ ] **Step 5: Encode password in config**

In `config.py`:

```python
from urllib.parse import quote
```

Then:

```python
if self.redis_password:
    password = quote(self.redis_password, safe="")
    return f"redis://:{password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest tests/test_redis_client.py tests/test_config.py -q
```

Expected:

```text
passed
```

---

## Task 7: Fix Public Metadata And Documentation Contract

**Files:**
- Modify: `src/plumelog_loguru/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Sync package metadata**

Change:

```python
__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"
```

to:

```python
__version__ = "0.2.0"
__author__ = "Alistar Max"
__email__ = "codingox@gmail.com"
```

- [ ] **Step 2: Correct README reliability language**

Replace absolute claims:

```markdown
确保在网络抖动时日志不丢失
```

with:

```markdown
在网络抖动时进行有限重试；重试耗尽后会记录丢弃数量，避免阻塞业务退出。
```

Replace:

```markdown
logger.info() 的调用耗时始终稳定在微秒级别，完全不影响业务响应速度
```

with:

```markdown
正常情况下日志提交只做本地转换和后台投递；当后台积压超过上限时会进入有界临时缓存并打印告警。
```

- [ ] **Step 3: Correct README config table**

Add missing rows:

```markdown
| `retry_delay` | `PLUMELOG_RETRY_DELAY` | `2.0` | 首次重试延迟，后续按指数退避 |
| `socket_connect_timeout` | `PLUMELOG_SOCKET_CONNECT_TIMEOUT` | `5.0` | Redis 连接建立超时 |
| `socket_timeout` | `PLUMELOG_SOCKET_TIMEOUT` | `5.0` | Redis 命令读写超时 |
| `temp_buffer_max_size` | `PLUMELOG_TEMP_BUFFER_MAX_SIZE` | `1000` | 临时缓存最大容量 |
```

- [ ] **Step 4: Correct README class diagram**

Ensure the class diagram uses real names:

```markdown
LogRecord: log_level, seq, dt_time
FieldExtractor: get_server_name, get_host_name, get_caller_info, get_next_seq
AsyncRedisClient: connect, send_log_record, send_log_records, disconnect
RedisSink: close, _ensure_initialized, _log_consumer
```

- [ ] **Step 5: Run metadata check**

Run:

```bash
uv run python - <<'PY'
import plumelog_loguru
print(plumelog_loguru.__version__)
print(plumelog_loguru.__author__)
print(plumelog_loguru.__email__)
PY
```

Expected:

```text
0.2.0
Alistar Max
codingox@gmail.com
```

---

## Task 8: Lint And Type Hygiene

**Files:**
- Modify only files touched by previous tasks unless a lint error blocks verification.

- [ ] **Step 1: Run ruff**

Run:

```bash
uv run --extra dev ruff check .
```

Expected after cleanup:

```text
All checks passed!
```

If ruff reports import sorting only, run:

```bash
uv run --extra dev ruff check . --fix
```

Review the diff before continuing.

- [ ] **Step 2: Run mypy**

Run:

```bash
uv run --extra dev mypy src tests
```

Expected target:

```text
Success: no issues found
```

If full `tests` strict typing is too broad for this repair, document the remaining test-only annotation errors and ensure at least:

```bash
uv run --extra dev mypy src
```

passes.

- [ ] **Step 3: Run full tests**

Run:

```bash
uv run pytest
```

Expected:

```text
passed
```

---

## Task 9: Final Review And Commit Grouping

**Files:**
- Review all modified files.

- [ ] **Step 1: Inspect diff**

Run:

```bash
git diff -- src/plumelog_loguru tests README.md pyproject.toml
```

Expected:

```text
Only runtime reliability, tests, docs, and metadata changes are present.
```

- [ ] **Step 2: Check worktree**

Run:

```bash
git status --short
```

Expected:

```text
Modified source/test/docs files plus this plan file.
Existing unrelated .antigravitycli/ remains untouched.
```

- [ ] **Step 3: Suggested commit split**

Use three commits:

```bash
git add src/plumelog_loguru/redis_sink.py tests/test_redis_sink.py
git commit -m "fix: bound redis sink shutdown and backpressure"

git add src/plumelog_loguru/redis_client.py src/plumelog_loguru/config.py src/plumelog_loguru/extractor.py tests/test_redis_client.py tests/test_config.py tests/test_extractor.py
git commit -m "fix: harden redis client errors and hot-path extraction"

git add README.md src/plumelog_loguru/__init__.py docs/superpowers/plans/2026-05-26-runtime-reliability-fix.md
git commit -m "docs: align plumelog runtime contract"
```

Do not add `.antigravitycli/`.

---

## Self-Review

- Spec coverage:
  - Performance issue: Task 3, Task 5.
  - Memory/resource leak: Task 2, Task 3, Task 5.
  - Design issue: Task 2, Task 4, Task 6, Task 7.
  - Test coverage: Task 1, Task 3, Task 6, Task 8.
- Placeholder scan:
  - No TBD/TODO placeholders.
  - Each implementation task has concrete file targets, code shape, commands, and expected result.
- Type consistency:
  - Uses existing names: `RedisSink`, `_AsyncRuntime`, `AsyncRedisClient`, `send_log_records`, `PlumelogSettings`, `LogRecord`.
