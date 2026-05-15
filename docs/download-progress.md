# 下载进度接口

## 终端输出

使用 `--verbose` 启动服务后，下载任务会逐条输出结构化进度日志，包括整体进度、当前分 P 进度、已下载大小、总大小和速度。

## SSE 使用流程

1. 创建任务：

```http
POST /api/task/bili
Content-Type: application/json

{"bid":"BV1xxxx","favid":"-1"}
```

响应中会返回 `task_id`。

2. 订阅任务事件：

```http
GET /api/tasks/{task_id}/events
Accept: text/event-stream
```

连接建立后会先收到最近一次任务快照，随后持续收到新事件。断线重连后会再次先收到最新快照。

## 事件

- `status`：任务开始、重试或状态变化。
- `progress`：下载进度更新。
- `completed`：任务完成。
- `failed`：任务失败。

`progress` 事件示例：

```text
event: progress
data: {"task_id":1,"bvid":"BV1xxxx","status":"downloading","overall_percent":62.5,"episode_index":2,"episode_count":3,"episode_name":"P2","episode_percent":87.5,"downloaded_bytes":73400320,"total_bytes":117440512,"speed_bytes_per_second":3145728.0}
```
