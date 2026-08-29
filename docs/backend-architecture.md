# 后端模块结构

FastAPI 应用由 `blsync.main` 创建，HTTP 适配层集中在 `blsync.routes`。业务代码不应从路由模块或 `blsync.main` 反向导入。

## 路由模块

| 模块 | 职责 | URL 范围 |
| --- | --- | --- |
| `routes/tasks.py` | 任务创建、查询、状态控制和 SSE 进度 | `/api/task/*`、`/api/tasks/*` |
| `routes/video.py` | Bilibili 视频信息查询 | `/api/video/*` |
| `routes/config.py` | 配置读取和更新 | `/api/config` |
| `routes/schemas/config.py` | 配置接口的响应 Schema、表单元数据和凭据脱敏展示 | — |
| `routes/files.py` | 已完成任务的文件列表和文件响应 | `/file/*` |
| `routes/frontend.py` | 前端入口、静态资源和 SPA fallback | `/`、`/{full_path}` |
| `routes/__init__.py` | 组合并导出应用所需的路由器 | — |

`main.py` 必须先注册 API 和文件路由，最后注册前端 catch-all 路由，避免 SPA fallback 拦截后端请求。

## 应用服务

收藏夹单次扫描位于 `services/favorite_scanner.py`。HTTP 的“立即扫描”端点和后台 producer 共同调用这个服务，因此二者共享同一个扫描锁和任务去重规则，同时避免 `routes/tasks.py` 反向导入 `main.py`。

## 配置边界

`configuration/models.py`、`loader.py` 和 `manager.py` 分别负责有效配置模型、CLI/TOML 加载和运行时配置生命周期。它们不依赖 HTTP 展示层。`routes/config.py` 将 manager 返回的配置快照转换成 `routes/schemas/config.py` 定义的脱敏响应，避免配置生命周期代码依赖前端表单结构。

## 新增接口

新增接口时，将处理函数放入对应的路由模块；如果逻辑同时被后台任务、CLI 或其他入口使用，应先提取到 `services` 或领域模块，再由路由调用。
