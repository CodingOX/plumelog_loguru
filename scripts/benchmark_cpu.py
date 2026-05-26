import time
import threading
import os
import sys
import gc
from unittest.mock import AsyncMock

try:
    import psutil
except ImportError:
    print("❌ 缺少依赖 `psutil`。请使用以下命令运行测试：")
    print("   uv run --with psutil python scripts/benchmark_cpu.py")
    sys.exit(1)

from loguru import logger
from plumelog_loguru import PlumelogSettings, RedisSink

# 🚀 补丁：Mock 掉 Redis 的网络发送过程，模拟一个零延迟的 Redis
# 这样我们可以纯粹地测试 plumelog-loguru 在 Python 侧的 CPU 开销（队列、线程、JSON序列化等）
from plumelog_loguru.redis_client import AsyncRedisClient
AsyncRedisClient.send_log_records = AsyncMock(return_value=True)

def monitor_resources(stop_event, stats):
    """后台监控 CPU 和内存的线程"""
    process = psutil.Process(os.getpid())
    # 丢弃第一次采集（通常为 0.0）
    process.cpu_percent(interval=None) 
    
    while not stop_event.is_set():
        time.sleep(0.1)
        # 获取当前进程的 CPU 使用率，多核可能超过 100%
        cpu = process.cpu_percent(interval=None)
        # 获取常驻内存 (RSS) MB
        mem = process.memory_info().rss / 1024 / 1024
        
        stats['cpu'].append(cpu)
        stats['mem'].append(mem)

def worker_sync(num_logs, thread_id):
    """模拟业务线程狂刷日志"""
    for i in range(num_logs):
        logger.info(f"[{thread_id}] 模拟业务请求订单号: ORD{i:08d} 发生状态流转，当前状态为: PROCESSING")

def run_benchmark(num_threads=4, logs_per_thread=25000):
    print("=" * 50)
    print("🚀 Plumelog-Loguru 极限压力测试 (纯 CPU 负载分析)")
    print("=" * 50)
    print(f"🧵 模拟业务线程数: {num_threads}")
    print(f"📝 每个线程日志数: {logs_per_thread}")
    print(f"📦 总计发送日志数: {num_threads * logs_per_thread}")
    print("配置: batch_size=500 (降低网络频次)")
    print("提示: 已 Mock Redis 网络层，专注分析组件本身的 CPU 损耗\n")
    
    # 1. 准备配置，关闭默认控制台输出
    logger.remove()
    config = PlumelogSettings(
        batch_size=500, 
        queue_max_size=200000,
        temp_buffer_max_size=10000
    )
    sink = RedisSink(config)
    
    # 2. 预热，确保事件循环线程启动
    logger.add(sink)
    logger.info("warmup")
    time.sleep(0.5) 
    
    # 3. 开启监控线程
    stop_event = threading.Event()
    stats = {'cpu': [], 'mem': []}
    monitor_thread = threading.Thread(target=monitor_resources, args=(stop_event, stats))
    monitor_thread.start()
    
    # 4. 开启业务压测线程
    threads = []
    print("⏳ 开始全力压测，请稍候...")
    start_time = time.time()
    
    for i in range(num_threads):
        t = threading.Thread(target=worker_sync, args=(logs_per_thread, i))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # 5. 等待队列排空并优雅关闭
    # 关键：close() 必须从主线程（非后台事件循环线程）调用。
    # 原因：close() 内部最终会调用 _runtime.stop() -> thread.join()，
    # 若通过 run_coroutine_threadsafe 把 close() 扔进后台事件循环线程，
    # 则线程将自己等待自己（join current thread），必然死锁。
    # 正确做法：主线程用 asyncio.run() 创建一个临时事件循环来驱动 close()，
    # close() 内部通过 _run_in_runtime 把清理工作委托给后台线程完成，
    # 最后 close() 在主线程侧调用 _runtime.stop() 顺利 join 后台线程。
    import asyncio
    asyncio.run(sink.close())
    
    end_time = time.time()
    stop_event.set()
    monitor_thread.join()
    
    # 6. 计算并输出结果
    duration = end_time - start_time
    total_logs = num_threads * logs_per_thread
    qps = total_logs / duration
    
    cpu_data = stats['cpu']
    mem_data = stats['mem']
    
    avg_cpu = sum(cpu_data) / len(cpu_data) if cpu_data else 0
    max_cpu = max(cpu_data) if cpu_data else 0
    max_mem = max(mem_data) if mem_data else 0
    avg_mem = sum(mem_data) / len(mem_data) if mem_data else 0

    core_count = psutil.cpu_count(logical=True)
    max_theoretical_cpu = core_count * 100

    print("\n" + "=" * 50)
    print("📊 压测结果报告")
    print("=" * 50)
    print(f"⏱️  总耗时:       {duration:.2f} 秒")
    print(f"⚡ 吞吐量(QPS): {qps:.0f} 条/秒")
    print("-" * 50)
    print(f"🖥️  CPU 峰值:    {max_cpu:.1f}% (系统总核数: {core_count}, 理论满载: {max_theoretical_cpu}%)")
    print(f"🖥️  CPU 平均:    {avg_cpu:.1f}%  <-- 重点关注这个指标")
    print(f"💾  内存 峰值:   {max_mem:.1f} MB")
    print(f"💾  内存 平均:   {avg_mem:.1f} MB")
    print("=" * 50)
    print("💡 结论参考：")
    print("1. Python 单核最高 CPU 为 100%。如果平均 CPU 在 30% 以下，说明极轻量。")
    print("2. 超过 100% 说明利用了多核（主业务线程占核心，日志后台线程占核心）。")
    print("3. 当 QPS 能达到数万以上且 CPU 占用合理时，普通应用的日志量（几百QPS）")
    print("   对其 CPU 的影响几乎可以忽略不计。")

if __name__ == "__main__":
    run_benchmark(num_threads=4, logs_per_thread=25000)
