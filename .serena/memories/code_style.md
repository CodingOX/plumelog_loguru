## 代码风格与规范
- 排版：Black（line-length=88）、isort profile=black；建议使用 UTF-8 与中文注释。
- 类型：严格使用类型提示，mypy 处于 strict 模式，禁止未标注返回值与未类型化定义。
- Lint：Ruff（E/W/F/I/B/C4/UP），保持现代 Python 写法与 bugbear 检查。
- 结构：模块化拆分，配置使用 Pydantic Settings，Redis 客户端采用异步接口，sink 遵循 Loguru sink 协议。
- 文档：README、docs 等均优先中文说明。