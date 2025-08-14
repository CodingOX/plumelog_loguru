#!/usr/bin/env python3
"""
plumelog 发布脚本
用于自动化构建和发布流程
"""

import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd: str, description: str) -> bool:
    """执行命令并返回是否成功"""
    print(f"🔄 {description}")
    print(f"   执行: {cmd}")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ {description} - 成功")
        if result.stdout.strip():
            print(f"   输出: {result.stdout.strip()}")
        return True
    else:
        print(f"❌ {description} - 失败")
        print(f"   错误: {result.stderr.strip()}")
        return False


def check_prerequisites() -> bool:
    """检查发布前置条件"""
    print("🔍 检查发布前置条件...")

    # 检查是否在项目根目录
    if not Path("pyproject.toml").exists():
        print("❌ 请在项目根目录运行此脚本")
        return False

    # 检查是否有未提交的更改
    result = subprocess.run(
        "git status --porcelain", shell=True, capture_output=True, text=True
    )
    if result.stdout.strip():
        print("⚠️  发现未提交的更改:")
        print(result.stdout)
        response = input("是否继续? (y/N): ")
        if response.lower() != "y":
            return False

    return True


def clean_build() -> bool:
    """清理构建目录"""
    print("🧹 清理构建目录...")

    # 删除旧的构建文件
    for pattern in ["dist", "build", "*.egg-info"]:
        if run_command(f"rm -rf {pattern}", f"删除 {pattern}"):
            continue
        else:
            return False

    return True


def build_package() -> bool:
    """构建包"""
    print("🔨 构建包...")

    # 使用uv构建
    return run_command("uv run python -m build", "构建包")


def check_package() -> bool:
    """检查包"""
    print("🔍 检查包...")

    # 使用twine检查
    return run_command("uv run twine check dist/*", "检查包")


def upload_to_testpypi() -> bool:
    """上传到TestPyPI"""
    print("🚀 上传到TestPyPI...")

    return run_command(
        "uv run twine upload --repository testpypi dist/*", "上传到TestPyPI"
    )


def upload_to_pypi() -> bool:
    """上传到PyPI"""
    print("🚀 上传到PyPI...")

    return run_command("uv run twine upload dist/*", "上传到PyPI")


def main():
    """主函数"""
    print("🎯 plumelog 发布脚本")
    print("=" * 50)

    # 检查前置条件
    if not check_prerequisites():
        sys.exit(1)

    # 清理构建目录
    if not clean_build():
        sys.exit(1)

    # 构建包
    if not build_package():
        sys.exit(1)

    # 检查包
    if not check_package():
        sys.exit(1)

    print("\n✅ 包构建和检查完成!")
    print("📦 构建文件位于 dist/ 目录")

    # 询问是否上传
    print("\n下一步操作:")
    print("1. 上传到TestPyPI (测试)")
    print("2. 上传到PyPI (正式发布)")
    print("3. 退出")

    choice = input("请选择 (1/2/3): ")

    if choice == "1":
        if upload_to_testpypi():
            print("\n✅ 已上传到TestPyPI!")
            print("🔗 访问: https://test.pypi.org/project/plumelog_loguru/")
            print("📦 测试安装: pip install -i https://test.pypi.org/simple/ plumelog_loguru")
    elif choice == "2":
        confirm = input("⚠️  确定要发布到正式PyPI吗? (y/N): ")
        if confirm.lower() == "y":
            if upload_to_pypi():
                print("\n🎉 已成功发布到PyPI!")
                print("🔗 访问: https://pypi.org/project/plumelog_loguru/")
                print("📦 安装: pip install plumelog_loguru")
    else:
        print("👋 退出发布流程")


if __name__ == "__main__":
    main()
