# Docker Image CI/CD Workflow

本文档说明 BLSync 项目的 Docker 镜像自动构建和发布流程。

## 功能概述

GitHub Actions 工作流会根据不同的触发条件自动构建和推送 Docker 镜像到 GitHub Container Registry (GHCR)。

## 触发条件

### 1. Push 到 main 分支
- **触发**: 当代码推送到 `main` 分支时
- **构建标签**: `latest`
- **推送**: ✅ 是
- **用途**: 保持最新稳定版本的镜像

### 2. 推送 Git Tag
- **触发**: 当推送格式为 `v*.*.*` 的标签时（例如 `v1.0.0`, `v2.3.4`）
- **构建标签**:
  - `v1.0.0` (完整版本号)
  - `v1.0` (主版本.次版本)
- **推送**: ✅ 是
- **用途**: 发布版本化的镜像

### 3. Pull Request
- **触发**: 当 PR 修改以下文件时:
  - `Dockerfile`
  - `pyproject.toml`
  - `uv.lock`
  - `.github/workflows/docker-publish.yml`
- **构建标签**: `pr-{number}` 和分支-commit
- **推送**: ❌ 否（仅构建验证）
- **用途**: 在合并前验证 Docker 镜像可以成功构建

### 4. 手动触发
- **触发**: 在 GitHub Actions 页面手动触发
- **行为**: 根据 `push: ${{ github.event_name != 'pull_request' }}` 条件决定是否推送

## 镜像信息

- **Registry**: `ghcr.io`
- **镜像名称**: `ghcr.io/{username}/blsync`
- **支持平台**:
  - `linux/amd64`
  - `linux/arm64`

## 使用示例

### 开发工作流

```bash
# 1. 创建功能分支
git checkout -b feature/my-feature

# 2. 进行开发并提交
git add .
git commit -m "Add new feature"

# 3. 推送到远程并创建 PR
git push origin feature/my-feature
# 在 GitHub 上创建 Pull Request

# 4. PR 触发工作流，自动构建验证镜像（不推送）
# 检查 GitHub Actions 页面确认构建成功
```

### 发布工作流

#### 方式一：通过 main 分支发布 latest 标签

```bash
# 1. 合并到 main
git checkout main
git merge feature/my-feature

# 2. 推送到 main（自动构建并推送 latest 标签）
git push origin main
```

**结果**: 构建并推送 `ghcr.io/{username}/blsync:latest`

#### 方式二：通过 Tag 发布版本

```bash
# 1. 确保在 main 分支或要发布的提交上
git checkout main

# 2. 创建版本标签
git tag v1.0.0

# 3. 推送标签（自动构建并推送版本标签）
git push origin v1.0.0
```

**结果**: 构建并推送以下标签:
- `ghcr.io/{username}/blsync:v1.0.0`
- `ghcr.io/{username}/blsync:v1.0`

## 拉取镜像

### 拉取最新版本
```bash
docker pull ghcr.io/{username}/blsync:latest
```

### 拉取特定版本
```bash
docker pull ghcr.io/{username}/blsync:v1.0.0
```

### 运行容器
```bash
docker run -d \
  --name blsync \
  -p 8000:8000 \
  -v /path/to/config:/app/config \
  ghcr.io/{username}/blsync:latest
```

## 工作流特性

### 1. 多平台构建
使用 Docker Buildx 的矩阵策略同时构建 amd64 和 arm64 架构的镜像。

### 2. 元数据标签
自动添加 OCI 标准标签，包括:
- 镜像标题和描述
- 仓库所有者
- 源代码链接
- 许可证信息
- Git 修订版本
- 创建时间戳

### 3. 缓存优化
使用 GitHub Actions 缓存加速构建过程。

### 4. 安全签名
- 为镜像生成签名证明（artifact attestation）
- 生成 SBOM（Software Bill of Materials）

### 5. 推送条件
- Pull Request: 只构建不推送
- Push 到 main: 构建并推送
- Tag 推送: 构建并推送
- 手动触发: 构建并推送

## 标签策略

| 事件类型 | 标签示例 | 推送 | 用途 |
|---------|---------|------|------|
| Push to main | `latest` | ✅ | 最新稳定版 |
| Tag `v1.2.3` | `v1.2.3`, `v1.2` | ✅ | 版本发布 |
| PR #42 | `pr-42`, `main-abc1234` | ❌ | PR 验证 |
| Commit | `{branch}-{short-sha}` | ✅ | Commit 追踪 |

## 配置说明

如需修改工作流行为，编辑 `.github/workflows/docker-publish.yml`:

### 修改支持的分支
```yaml
on:
  push:
    branches:
      - main
      - dev  # 添加其他分支
```

### 修改 Tag 模式
```yaml
on:
  tags:
      - 'v*.*.*'
      - 'release-*'  # 添加其他模式
```

### 修改镜像名称
```yaml
env:
  IMAGE_NAME: ${{ github.repository }}  # 默认使用仓库名
  # 或使用自定义名称
  IMAGE_NAME: your-org/your-image-name
```

### 添加更多平台
```yaml
strategy:
  matrix:
    platform:
      - linux/amd64
      - linux/arm64
      - linux/arm/v7  # 添加 32-bit ARM
```

## 故障排查

### 构建失败
1. 检查 GitHub Actions 日志
2. 验证 `Dockerfile` 语法正确
3. 确保依赖文件（`pyproject.toml`, `uv.lock`）有效

### 推送失败
1. 确认仓库设置中启用了 Actions
2. 检查 `GITHUB_TOKEN` 权限（需要 `packages: write`）
3. 验证镜像名称格式正确

### 镜像过大
1. 考虑使用多阶段构建
2. 清理不必要的缓存和文件
3. 使用 Alpine 基础镜像（当前已使用）

### 权限问题
确保 GitHub 仓库设置中:
1. Settings → Actions → General → Workflow permissions
2. 选择 "Read and write permissions"

## 相关文件

- [Dockerfile](../Dockerfile) - Docker 镜像定义
- [docker_build_push.py](../docker_build_push.py) - 本地构建脚本
- [.github/workflows/docker-publish.yml](../.github/workflows/docker-publish.yml) - 工作流定义

## 参考

基于 [astral-sh/uv](https://github.com/astral-sh/uv) 项目的 Docker 构建流程设计。
