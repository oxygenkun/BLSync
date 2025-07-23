# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 添加任务超时控制机制，防止任务长时间挂起
- 实现并发下载任务处理
- 添加任务状态跟踪，防止重复任务和资源浪费

### Changed
- 优化任务队列管理，提升并发处理能力
- 改进配置文件模板说明和文档结构
- 定义明确的Python版本要求

### Fixed
- 修复任务队列中可能出现的死锁问题
- 优化并发处理中的资源管理

## [0.2.0] - 2025-06-27

### Added
- 引入模块化的消费者架构（Consumer Pattern），支持不同类型的任务处理
- 添加后处理功能（PostProcessor），支持下载后操作：
  - `move`：将视频从一个收藏夹移动到另一个收藏夹
  - `remove`：从收藏夹中移除视频
- 支持复杂配置格式，允许为收藏夹设置任务名和后处理操作
- 支持多分P视频的批量下载模式检测
- 添加视频封面下载功能，与视频同时保存
- 支持详细输出模式（verbose），便于调试和监控

### Changed
- 重构下载器模块，将功能拆分到独立的消费者模块中
- 迁移包管理器从 rye 到 uv，提升构建和依赖管理效率
- 优化配置文件结构和模板说明
- 改进全局配置加载机制
- 更新依赖库版本

### Fixed
- 修复默认配置文件路径问题
- 优化下载显示逻辑，改善用户体验
- 修复不必要的凭据字段处理
- 修复CLI参数使用问题

## [0.1.2] - 2025-01-02

### Added
- 添加视频封面下载功能，在同步时自动保存视频封面

### Changed
- 优化视频信息获取逻辑，避免重复请求
- 更新 ruff 代码规范配置

### Fixed
- 修复配置文件默认路径设置
- 优化不必要凭据字段的处理逻辑

## [0.1.1] - 2024-11-22

### Added
- 完善 README 文档，添加详细的使用说明
- 添加 Docker Compose 开发环境配置
- 支持详细输出模式（verbose），便于调试

### Changed
- 重命名开发环境 compose 文件
- 更新构建脚本和容器配置

### Fixed
- 修复容器镜像构建中缺少配置文件的问题
- 优化循环速度，减少资源消耗

## [0.1.0] - 2024-11-13

### Added
- **核心功能**：Bilibili 收藏夹同步功能
- **REST API**：外部 API 下载请求支持
- **视频下载**：基于 yutto 的视频下载功能
- **数据库支持**：SQLite 数据库存储下载历史，避免重复下载
- **身份验证**：Bilibili 账号凭据管理（sessdata, bili_jct 等）
- **配置管理**：TOML 格式配置文件支持
- **Docker 支持**：完整的 Docker 容器化部署方案
- **多收藏夹**：支持多个收藏夹的同时同步
- **FastAPI Web服务**：提供 HTTP API 接口
- **日志系统**：基于 loguru 的结构化日志记录
- **元数据支持**：下载视频封面和元数据信息
- 自动扫描指定收藏夹中的视频
- 增量下载，跳过已下载的视频
- 支持视频元数据（.nfo 文件）和封面图片下载
- 可配置的扫描间隔和超时设置
- RESTful API 接口用于外部调用
- Python 3.12+ 支持
- 异步下载处理，提升性能
- 模块化代码结构，易于维护和扩展
- 完整的错误处理和日志记录
- Docker 容器化部署，支持多架构

[Unreleased]: https://github.com/oxygenkun/BLSync/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/oxygenkun/BLSync/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/oxygenkun/BLSync/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/oxygenkun/BLSync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/oxygenkun/BLSync/releases/tag/v0.1.0
