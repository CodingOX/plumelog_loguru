"""示例：如何使用plumelog-loguru

演示基本的使用方法和配置选项。
"""

import asyncio
from loguru import logger
from plumelog_loguru import create_redis_sink, PlumelogSettings, RedisSink


async def basic_example():
    """基本使用示例"""
    print("=== 基本使用示例 ===")

    # 使用默认配置创建Redis sink
    redis_sink = create_redis_sink()
    logger.add(redis_sink, level="INFO")  # type: ignore[arg-type]

    # 记录不同级别的日志
    logger.info("这是一条信息日志")
    logger.warning("这是一条警告日志")
    logger.error("这是一条错误日志")

    # 等待一段时间让日志发送完成
    await asyncio.sleep(3)
    print("基本示例完成\n")


async def custom_config_example():
    """自定义配置示例"""
    print("=== 自定义配置示例 ===")

    # 创建自定义配置
    config = PlumelogSettings(
        app_name="示例应用",
        env="开发环境",
        redis_host="localhost",
        redis_port=6379,
        redis_db=0,
        batch_size=50,
        batch_interval_seconds=1.0,
    )

    # 使用自定义配置
    redis_sink = create_redis_sink(config)
    logger.add(redis_sink, level="DEBUG")  # type: ignore[arg-type]

    # 记录一些日志
    logger.debug("调试信息")
    logger.info("应用启动成功")
    logger.success("操作完成")

    await asyncio.sleep(2)
    print("自定义配置示例完成\n")


async def context_manager_example():
    """上下文管理器示例"""
    print("=== 上下文管理器示例 ===")

    config = PlumelogSettings(app_name="上下文示例", env="测试")

    # 使用异步上下文管理器
    async with RedisSink(config) as sink:
        logger.add(sink)  # type: ignore

        logger.info("在上下文管理器中记录日志")
        logger.warning("这将自动处理资源清理")

        await asyncio.sleep(1)

    print("上下文管理器示例完成（资源已自动清理）\n")


def class_method_example():
    """类方法调用示例"""
    print("=== 类方法调用示例 ===")

    class ExampleService:
        def __init__(self):
            self.name = "示例服务"

        def process_data(self, data: str):
            """处理数据的方法"""
            logger.info(f"开始处理数据: {data}")

            # 模拟一些处理逻辑
            if not data:
                logger.error("数据为空，无法处理")
                return False

            logger.success(f"数据处理成功: {data}")
            return True

        def handle_error(self):
            """错误处理示例"""
            try:
                # 模拟一个错误
                result = 1 / 0
            except ZeroDivisionError as e:
                logger.exception(f"发生除零错误: {e}")
                return False

    # 创建服务实例并调用方法
    service = ExampleService()
    service.process_data("测试数据")
    service.process_data("")
    service.handle_error()

    print("类方法调用示例完成\n")


async def main():
    """主函数：运行所有示例"""
    print("🚀 Plumelog-Loguru 使用示例\n")

    # 设置Redis sink（使用默认配置用于所有示例）
    default_sink = create_redis_sink()
    logger.add(default_sink, level="DEBUG")  # type: ignore[arg-type]

    try:
        # 运行各种示例
        await basic_example()
        await custom_config_example()
        await context_manager_example()
        class_method_example()

        print("✅ 所有示例运行完成！")
        print("请检查Redis中的日志队列以验证日志传输。")

    except Exception as e:
        logger.exception(f"运行示例时发生错误: {e}")

    finally:
        # 给足时间让所有日志发送完成
        print("等待日志发送完成...")
        await asyncio.sleep(5)


if __name__ == "__main__":
    # 注意：这个示例需要Redis服务运行在localhost:6379
    # 如果Redis不可用，日志将会显示连接错误，但程序不会崩溃
    asyncio.run(main())
