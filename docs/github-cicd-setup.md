# GitHub CI/CD 配置说明

## 工作流概览

| 文件 | 触发条件 | 作用 |
|------|----------|------|
| `ci.yml` | push main / PR | 矩阵测试 + lint + type check |
| `release.yml` | push `v*` tag | 测试 → 构建 → 发布 PyPI |

---

## 方式一：Trusted Publishing（推荐，无需 Token）

这是现代最佳实践，PyPI 通过 GitHub OIDC 验证身份，完全不需要在 GitHub 里设置任何 Secret。

> **当前项目采用此方式。**

### 第一步：在 PyPI 添加 Trusted Publisher

1. 登录 [pypi.org](https://pypi.org)，进入项目页面：[plumelog-loguru/publishing](https://pypi.org/manage/project/plumelog-loguru/settings/publishing/)
2. 点击左侧菜单 **Publishing** → **Add a new publisher**，填写：

| 字段 | 填写内容 |
|------|----------|
| **Owner** | `CodingOX`（GitHub 用户名/组织名） |
| **Repository name** | `plumelog-loguru` |
| **Workflow name** | `release.yml` |
| **Environment name** | `pypi` |

3. 点击 **Add** 保存

> ⚠️ 如果是**全新项目、还没在 PyPI 创建过**，需去
> [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing)
> 添加 **Pending Publisher**，首次发布时会自动创建项目。

### 第二步：在 GitHub 创建 `pypi` Environment

1. repo → **Settings** → **Environments** → **New environment**
2. 名称填 `pypi`（必须与 PyPI 配置里的 Environment name 完全一致）
3. 可选：开启 **Required reviewers** 增加安全性（每次发布需人工审批）

### 第三步：Workflow 关键配置（已就绪）

`release.yml` 的 publish job 已按如下方式配置，无需修改：

```yaml
publish:
  runs-on: ubuntu-latest
  environment:
    name: pypi                              # 必须与 PyPI 上填的 Environment name 一致
    url: https://pypi.org/p/plumelog-loguru
  permissions:
    id-token: write                         # 必须！用于获取 OIDC token
  steps:
    - uses: astral-sh/setup-uv@v5
    - uses: actions/download-artifact@v4
      with:
        name: dist
        path: dist/
    - run: uv publish                       # 无需 token，自动通过 OIDC 认证
```

---

## 方式二：传统 API Token（备选）

如果不想用 Trusted Publishing，可手动配置 Token。

### 步骤

1. 登录 PyPI → **Account Settings** → **API tokens** → **Add API token**
2. 复制生成的 token（只显示一次！格式为 `pypi-xxxx...`）
3. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
   - Name: `PYPI_API_TOKEN`
   - Value: 粘贴 token
4. `release.yml` 中将 publish 步骤改为：

```yaml
- run: uv publish --token ${{ secrets.PYPI_API_TOKEN }}
```

> 同时移除 `permissions: id-token: write`（此方式不需要）

---

## 发布流程

```bash
# 1. 确认代码已合并到 main，本地测试通过
uv run pytest tests/ -v

# 2. 更新 pyproject.toml 中的 version 字段
# version = "0.3.0"

# 3. 打 tag 并推送（触发 release.yml）
git tag -a v0.3.0 -m "v0.3.0: 描述本次变更"
git push origin v0.3.0
```

推送 tag 后，GitHub Actions 自动执行：
1. ✅ Python 3.10 / 3.11 / 3.12 矩阵测试 + ruff + mypy
2. 📦 构建 wheel + sdist
3. 🚀 通过 OIDC 发布到 PyPI（无需任何 Secret）
