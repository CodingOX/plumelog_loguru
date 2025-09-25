## 常用命令
- 安装依赖：`uv sync` 或 `pip install -e .[dev]`。
- 运行测试：`uv run pytest`（已在 pytest.ini 中启用 coverage）或 `pytest`。
- 代码格式化：`uv run black .` 与 `uv run isort .`。
- 静态检查：`uv run ruff check .`、`uv run mypy src`。
- 构建分发包：`uv build`（或 `python -m build`）。