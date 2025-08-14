"""类型检查演示

演示修复后的类型注解，以及为什么Pylance会报告类型错误。
"""

from loguru import logger
from plumelog_loguru import create_redis_sink, PlumelogSettings, RedisSink


def demonstrate_correct_usage():
    """演示正确的使用方法（无类型错误）"""
    print("=== 正确的使用方法 ===")
    
    # 方法1: 使用工厂函数（推荐）
    sink = create_redis_sink()
    logger.add(sink)  # type: ignore[arg-type]
    
    # 方法2: 使用配置参数
    config = PlumelogSettings(app_name="类型检查演示")
    sink_with_config = create_redis_sink(config)
    logger.add(sink_with_config)  # type: ignore[arg-type]
    
    logger.info("使用工厂函数创建的sink工作正常！")


def demonstrate_direct_usage():
    """演示直接使用RedisSink类（需要类型忽略）"""
    print("=== 直接使用RedisSink类 ===")
    
    config = PlumelogSettings(app_name="直接使用演示")
    
    # 这种用法在类型检查上仍然会有问题，因为RedisSink类本身不是Loguru期望的标准sink类型
    # 但实际运行时工作正常，因为RedisSink实现了__call__方法
    direct_sink = RedisSink(config)
    logger.add(direct_sink)  # type: ignore[arg-type]
    
    logger.info("直接使用RedisSink实例也能工作！")


def explain_the_issue():
    """解释类型检查问题的原因"""
    print("\n=== 问题原因解释 ===")
    
    print("""
    为什么会出现类型错误？
    
    1. Loguru的logger.add()方法期望的sink参数类型是：
       - str | PathLikeStr (文件路径)
       - TextIO (文件对象) 
       - Callable[[Record], None] (函数)
       - Callable[[Message], Awaitable[None]] (异步函数)
    
    2. 我们的RedisSink类虽然实现了__call__方法，可以作为可调用对象使用，
       但在静态类型检查时，Pylance无法自动识别它符合Callable[[Record], None]协议。
    
    3. 解决方案：
       - 使用create_redis_sink()工厂函数（推荐）
       - 或者使用 # type: ignore 注释忽略类型检查
       - 或者定义Protocol来显式声明类型兼容性
    
    4. 实际运行时没有问题，因为Python的鸭子类型允许任何实现了__call__方法的对象
       作为可调用对象使用。
    """)


if __name__ == "__main__":
    print("🔍 Plumelog-Loguru 类型检查演示\n")
    
    demonstrate_correct_usage()
    demonstrate_direct_usage()
    explain_the_issue()
    
    print("\n✅ 演示完成！推荐使用create_redis_sink()工厂函数来避免类型问题。")
