"""
FastAPI routes and request handlers.
"""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from blsync import get_global_configs
from blsync.consumer.bilibili import VIDEO_FILE_SUFFIXES, BiliVideoTaskContext
from blsync.database import get_task_dal
from blsync.model.task import TaskModel, TaskStatus
from blsync.progress import get_progress_broker
from blsync.scraper import BScraper

# 支持通过环境变量指定项目根目录，默认使用相对路径计算
# 本地开发: 自动计算，Docker: 通过环境变量设置为 /app
BASE_DIR = Path(os.environ.get("BLSYNC_BASE_DIR", Path(__file__).parents[2]))
STATIC_DIR = BASE_DIR / "static"

# API 路由器（带 /api 前缀）
api_router = APIRouter()

# 文件路由器（不带 /api 前缀）
file_router = APIRouter()

# 前端路由器（不带前缀）
frontend_router = APIRouter()


class TaskRequest(BaseModel):
    bid: str
    favid: str = "-1"  # 默认值为-1表示没有收藏夹id
    selected_episodes: list[int] | None = None  # 选中的分P索引列表


class UpdateTaskStatusRequest(BaseModel):
    status: str  # 新状态：ready, consuming, downloading, done, failed
    error_message: str | None = None  # 失败时的错误信息（可选）


class BatchUpdateTaskStatusRequest(BaseModel):
    task_ids: list[int]  # 任务 id 列表
    status: str  # 新状态：ready, consuming, downloading, done, failed
    error_message: str | None = None  # 失败时的错误信息（可选）


class BatchDeleteTasksRequest(BaseModel):
    task_ids: list[int]  # 任务 id 列表


VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".flv": "video/x-flv",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def _task_downloaded_video_files(task: TaskModel) -> list[Path]:
    downloaded_files = task.task_context_dict.get("downloaded_files", [])
    if not isinstance(downloaded_files, list):
        return []

    files: list[Path] = []
    for path_value in downloaded_files:
        if not isinstance(path_value, str):
            continue

        path = Path(path_value)
        if path.suffix.lower() not in VIDEO_FILE_SUFFIXES:
            continue
        if not path.exists() or not path.is_file():
            continue

        files.append(path)

    return files


def _downloaded_video_files_from_task_data(task_data: str) -> list[Path]:
    try:
        downloaded_files = json.loads(task_data).get("downloaded_files", [])
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(downloaded_files, list):
        return []

    files: list[Path] = []
    for path_value in downloaded_files:
        if not isinstance(path_value, str):
            continue

        path = Path(path_value)
        if path.suffix.lower() not in VIDEO_FILE_SUFFIXES:
            continue
        if not path.exists() or not path.is_file():
            continue

        files.append(path)

    return files


def _task_file_summaries(task_id: int, files: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "name": path.name,
            "size": path.stat().st_size,
            "download_url": f"/file/{task_id}/{index}",
        }
        for index, path in enumerate(files)
    ]


async def _get_completed_task_for_file(task_id: int) -> TaskModel:
    task_dal = get_task_dal()
    task = await task_dal.get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.status != TaskStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail=f"Task {task_id} is not completed")
    return task


@file_router.get("/file/{task_id}", tags=["文件"], summary="获取任务下载文件列表")
async def get_task_files(task_id: int):
    """Return video files recorded for a completed task."""
    task = await _get_completed_task_for_file(task_id)
    files = _task_downloaded_video_files(task)
    if not files:
        raise HTTPException(status_code=404, detail="No downloaded video files found")

    return {
        "task_id": task_id,
        "files": _task_file_summaries(task_id, files),
    }


@file_router.get(
    "/file/{task_id}/{file_index}",
    tags=["文件"],
    summary="下载任务视频文件",
)
async def download_task_file(task_id: int, file_index: int) -> FileResponse:
    """Download one recorded video file by task id and file index."""
    task = await _get_completed_task_for_file(task_id)
    files = _task_downloaded_video_files(task)
    if file_index < 0 or file_index >= len(files):
        raise HTTPException(status_code=404, detail="Downloaded file not found")

    path = files[file_index]
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type=VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        content_disposition_type="inline",
    )


@frontend_router.get("/", tags=["前端"], summary="前端页面")
async def read_root() -> FileResponse:
    """
    返回前端页面

    访问此接口将返回 BLSync 的前端管理界面，用于提交 Bilibili 视频下载任务。
    """
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    else:
        raise HTTPException(status_code=404, detail="Frontend page not found")


# Catch-all route for SPA (Single Page Application) routing
# 所有其他路由都返回 index.html，由 React Router 处理
@frontend_router.get("/{full_path:path}", tags=["前端"], summary="SPA 路由")
async def spa_fallback(full_path: str) -> FileResponse:
    """
    支持 React Router 的客户端路由

    对于任何不是 API 请求的路由，返回 index.html。
    这样 React Router 可以在前端处理路由（如 /tasks, /add-task 等）。

    注意：直接处理 /assets 路径的静态文件请求。
    """
    # 处理静态资源文件
    if full_path.startswith("assets/"):
        asset_path = STATIC_DIR / full_path
        if asset_path.exists() and asset_path.is_file():
            return FileResponse(str(asset_path))

    # 其他所有路径返回 index.html，由 React Router 处理
    index_file = STATIC_DIR / "index.html"
    logger.info(index_file)
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    else:
        raise HTTPException(status_code=404, detail="Frontend page not found")


@api_router.post("/task/bili", tags=["任务"], summary="创建 Bilibili 下载任务")
async def create_task(task: TaskRequest):
    """
    创建 Bilibili 视频下载任务

    任务创建逻辑：
    1. 检查数据库中是否已存在该任务
    2. 若存在且指定了selected_episodes，更新任务上下文
    3. 若存在且状态为FAILED/DONE，重置为READY
    4. 若不存在，创建新任务到数据库
    """
    try:
        task_dal = get_task_dal()

        # 创建任务上下文
        task_context = BiliVideoTaskContext(
            bid=task.bid,
            task_name=task.favid,
        )

        # 将 selected_episodes 添加到任务上下文中
        task_context_dict = task_context.model_dump()
        if task.selected_episodes is not None:
            task_context_dict["selected_episodes"] = task.selected_episodes

        # Check if task already exists
        existing_status = await task_dal.get_bili_video_task_status(
            task.bid, task.favid
        )

        if existing_status is not None:
            # 任务已存在，更新任务上下文
            reset_status = existing_status in (TaskStatus.FAILED, TaskStatus.COMPLETED)
            task_model = await task_dal.update_bili_video_task(
                bvid=task.bid,
                favid=task.favid,
                task_context=task_context_dict,
                reset_status=reset_status,
            )

            if reset_status:
                return {
                    "status": "updated",
                    "message": f"Task {task.bid} updated and reset to ready",
                    "task_id": task_model.id if task_model else None,
                }
            else:
                return {
                    "status": "updated",
                    "message": f"Task {task.bid} context updated (status: {existing_status.value})",
                    "task_id": task_model.id if task_model else None,
                }

        # Create task in database
        task_model = await task_dal.create_bili_video_task(
            bvid=task.bid,
            favid=task.favid,
            task_context=task_context_dict,
        )
        return {
            "status": "success",
            "message": f"Task {task.bid} added to database",
            "task_id": task_model.id,
        }

    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/tasks/status", tags=["任务"], summary="获取任务队列状态")
async def get_task_status():
    """
    获取当前任务队列的状态信息

    返回各状态任务的数量统计。
    """
    task_dal = get_task_dal()
    stats = await task_dal.get_task_stats()

    return {
        "ready": stats[TaskStatus.READY.value],
        "consuming": stats[TaskStatus.CONSUMING.value],
        "downloading": stats[TaskStatus.DOWNLOADING.value],
        "pausing": stats[TaskStatus.PAUSING.value],
        "paused": stats[TaskStatus.PAUSED.value],
        "completed": stats[TaskStatus.COMPLETED.value],
        "failed": stats[TaskStatus.FAILED.value],
    }


@api_router.post("/tasks/scan", tags=["任务"], summary="立即扫描收藏夹")
async def scan_tasks():
    """Trigger one immediate favorite scan using the producer workflow."""
    from blsync.main import scan_favorites_once

    try:
        return await scan_favorites_once()
    except Exception as e:
        logger.error(f"Error scanning favorites: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/video/info", tags=["视频"], summary="获取视频详细信息")
async def get_video_info(bvid: str = Query(..., description="视频BV号")):
    """
    根据 BV 号获取视频详细信息，包括标题、封面、作者、分P列表等。
    """
    config = get_global_configs()
    scraper = BScraper(config)

    video_info = await scraper.get_video_info(bvid)
    if video_info is None:
        raise HTTPException(status_code=404, detail="视频不存在或已失效")

    return {
        "bvid": bvid,
        "title": video_info.get("title"),
        "pic": video_info.get("pic"),
        "desc": video_info.get("desc"),
        "videos": video_info.get("videos", 1),  # 分P数量
        "pages": video_info.get("pages", []),  # 分P详情列表
        "owner": {
            "name": video_info.get("owner", {}).get("name"),
            "face": video_info.get("owner", {}).get("face"),
        },
    }


@api_router.get("/tasks", tags=["任务"], summary="分页获取任务列表")
async def get_tasks(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: str | None = Query(None, description="状态筛选"),
):
    """
    分页获取任务列表，支持按状态筛选。
    """
    task_dal = get_task_dal()

    # 验证 status 参数
    valid_statuses = {s.value for s in TaskStatus}
    if status and status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Valid values are: {', '.join(valid_statuses)}",
        )

    result = await task_dal.get_tasks_paginated(
        page=page, page_size=page_size, status=status
    )
    for item in result["items"]:
        item["files"] = []
        if item["status"] != TaskStatus.COMPLETED.value:
            continue
        files = _downloaded_video_files_from_task_data(item["task_data"])
        item["files"] = _task_file_summaries(item["id"], files)

    return result


@api_router.get("/tasks/events", tags=["任务"], summary="订阅任务变更")
async def stream_all_task_events():
    """Stream future task events across the whole queue as SSE."""

    async def event_stream():
        subscription = get_progress_broker().subscribe_all()
        iterator = subscription.__aiter__()
        next_event = asyncio.create_task(iterator.__anext__())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(next_event), timeout=15
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield (
                    f"event: {event.event.value}\n"
                    f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
                )
                next_event = asyncio.create_task(iterator.__anext__())
        finally:
            next_event.cancel()
            with suppress(asyncio.CancelledError):
                await next_event
            await subscription.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api_router.get("/tasks/{task_id}", tags=["任务"], summary="获取任务详情")
async def get_task_detail(task_id: int):
    """
    获取单个任务的详细信息。
    """
    task_dal = get_task_dal()

    async with task_dal.async_session() as session:
        from blsync.model.task import TaskModel, select

        stmt = select(TaskModel).where(TaskModel.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return task_dal._task_to_dict(task)


@api_router.get("/tasks/{task_id}/events", tags=["任务"], summary="订阅任务进度")
async def stream_task_events(task_id: int):
    """Stream latest and future progress events for one task as SSE."""
    task_dal = get_task_dal()
    async with task_dal.async_session() as session:
        from blsync.model.task import TaskModel, select

        stmt = select(TaskModel.id).where(TaskModel.id == task_id)
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def event_stream():
        subscription = get_progress_broker().subscribe(task_id)
        iterator = subscription.__aiter__()
        next_event = asyncio.create_task(iterator.__anext__())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(next_event), timeout=15
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield (
                    f"event: {event.event.value}\n"
                    f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
                )
                next_event = asyncio.create_task(iterator.__anext__())
        finally:
            next_event.cancel()
            with suppress(asyncio.CancelledError):
                await next_event
            await subscription.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api_router.put("/tasks/{task_id}/status", tags=["任务"], summary="手动修改任务状态")
async def update_task_status(task_id: int, request: UpdateTaskStatusRequest):
    """
    手动修改任务状态。

    支持的状态值：
    - ready: 待处理
    - consuming: 消费中
    - downloading: 下载中
    - completed: 已完成
    - failed: 失败

    当状态设置为 failed 时，可以附带 error_message 说明失败原因。
    """
    # 验证状态值是否有效
    valid_statuses = {s.value for s in TaskStatus}
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{request.status}'. Valid values are: {', '.join(valid_statuses)}",
        )

    task_dal = get_task_dal()

    # 通过 task_id 获取任务
    async with task_dal.async_session() as session:
        from blsync.model.task import TaskModel, select

        stmt = select(TaskModel).where(TaskModel.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # 验证：如果设置为 failed，必须有错误信息（可选）
        new_status = TaskStatus(request.status)
        if new_status == TaskStatus.FAILED and request.error_message:
            session.add(task)
            task.status = new_status.value
            task.error_message = request.error_message
        elif new_status == TaskStatus.FAILED:
            raise HTTPException(
                status_code=400,
                detail="error_message is required when status is 'failed'",
            )
        elif new_status == TaskStatus.COMPLETED:
            session.add(task)
            task.status = new_status.value
            task.completed_at = task.updated_at
            task.error_message = None
        else:
            session.add(task)
            task.status = new_status.value

        await session.commit()
        await session.refresh(task)
        task_dal._publish_status_event(task, new_status, request.error_message)

        return task_dal._task_to_dict(task)


@api_router.put("/tasks/status", tags=["任务"], summary="批量修改任务状态")
async def batch_update_task_status(request: BatchUpdateTaskStatusRequest):
    """
    批量修改任务状态。

    校验规则与单项修改一致：状态值必须合法；设置为 failed 时必须附带 error_message。
    部分任务失败不影响其余任务，响应中分别返回成功和失败的 id 列表。
    """
    valid_statuses = {s.value for s in TaskStatus}
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{request.status}'. Valid values are: {', '.join(valid_statuses)}",
        )

    new_status = TaskStatus(request.status)
    if new_status == TaskStatus.FAILED and not request.error_message:
        raise HTTPException(
            status_code=400,
            detail="error_message is required when status is 'failed'",
        )

    task_dal = get_task_dal()

    async with task_dal.async_session() as session:
        from blsync.model.task import TaskModel, select

        stmt = select(TaskModel).where(TaskModel.id.in_(request.task_ids))
        result = await session.execute(stmt)
        tasks = list(result.scalars().all())

        found_ids = {task.id for task in tasks}
        failed = [
            {"task_id": task_id, "detail": f"Task {task_id} not found"}
            for task_id in request.task_ids
            if task_id not in found_ids
        ]

        for task in tasks:
            session.add(task)
            task.status = new_status.value
            if new_status == TaskStatus.FAILED:
                task.error_message = request.error_message
            elif new_status == TaskStatus.COMPLETED:
                task.completed_at = task.updated_at
                task.error_message = None

        await session.commit()

        for task in tasks:
            await session.refresh(task)
            task_dal._publish_status_event(task, new_status, request.error_message)

        return {"succeeded": [task.id for task in tasks], "failed": failed}


@api_router.delete("/tasks", tags=["任务"], summary="批量删除任务")
async def batch_delete_tasks(request: BatchDeleteTasksRequest):
    """
    批量删除任务。

    不存在的 id 会计入 failed 列表，其余任务照常删除。
    """
    task_dal = get_task_dal()

    async with task_dal.async_session() as session:
        from blsync.model.task import TaskModel, delete, select

        stmt = select(TaskModel.id).where(TaskModel.id.in_(request.task_ids))
        result = await session.execute(stmt)
        found_ids = set(result.scalars().all())

        failed = [
            {"task_id": task_id, "detail": f"Task {task_id} not found"}
            for task_id in request.task_ids
            if task_id not in found_ids
        ]

        if found_ids:
            await session.execute(delete(TaskModel).where(TaskModel.id.in_(found_ids)))
            await session.commit()

        return {"succeeded": sorted(found_ids), "failed": failed}


@api_router.post("/tasks/{task_id}/pause", tags=["任务"], summary="暂停任务")
async def pause_task(task_id: int):
    """
    暂停一个任务。

    - ready：直接置为 paused，不再被调度。
    - consuming/downloading：记录暂停请求并协作式取消正在运行的下载，
      已下载的分片保留在磁盘上，继续下载时断点续传。
    - completed/failed/paused：不可暂停，返回 409。
    """
    task_dal = get_task_dal()
    task = await task_dal.get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    pausable = {
        TaskStatus.READY.value,
        TaskStatus.CONSUMING.value,
        TaskStatus.DOWNLOADING.value,
    }
    if task.status not in pausable:
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} with status '{task.status}' cannot be paused",
        )

    updated = await task_dal.request_task_pause(task_id)
    return task_dal._task_to_dict(updated)


@api_router.post("/tasks/{task_id}/resume", tags=["任务"], summary="继续任务")
async def resume_task(task_id: int):
    """
    继续一个已暂停的任务：置回 ready 由 consumer 重新调度，下载断点续传。
    """
    task_dal = get_task_dal()
    task = await task_dal.get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.status != TaskStatus.PAUSED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} with status '{task.status}' cannot be resumed",
        )

    updated = await task_dal.resume_paused_task(task_id)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} is still owned and cannot be resumed",
        )
    return task_dal._task_to_dict(updated)


def start_server():
    import uvicorn

    from blsync.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000)
