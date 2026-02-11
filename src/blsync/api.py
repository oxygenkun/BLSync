"""
FastAPI routes and request handlers.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from blsync import get_global_configs
from blsync.consumer.bilibili import BiliVideoTaskContext
from blsync.database import get_task_dal
from blsync.scraper import BScraper
from blsync.task_models import TaskStatus

BASE_DIR = Path(__file__).parents[2]
STATIC_DIR = BASE_DIR / "static"

# API 路由器（带 /api 前缀）
api_router = APIRouter()

# 前端路由器（不带前缀）
frontend_router = APIRouter()


class TaskRequest(BaseModel):
    bid: str
    favid: str = "-1"  # 默认值为-1表示没有收藏夹id
    selected_episodes: list[int] | None = None  # 选中的分P索引列表


class UpdateTaskStatusRequest(BaseModel):
    status: str  # 新状态：pending, executing, completed, failed
    error_message: str | None = None  # 失败时的错误信息（可选）


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


@api_router.post("/task/bili", tags=["任务"], summary="创建 Bilibili 下载任务")
async def create_task(task: TaskRequest):
    """
    创建 Bilibili 视频下载任务

    任务创建逻辑：
    1. 检查数据库中是否已存在该任务
    2. 若存在且指定了selected_episodes，更新任务上下文
    3. 若存在且状态为FAILED/COMPLETED，重置为PENDING
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
        existing_status = await task_dal.get_bili_video_task_status(task.bid, task.favid)

        if existing_status is not None:
            # 任务已存在，更新任务上下文
            reset_status = existing_status in (TaskStatus.FAILED, TaskStatus.COMPLETED)
            await task_dal.update_bili_video_task(
                bvid=task.bid,
                favid=task.favid,
                task_context=task_context_dict,
                reset_status=reset_status,
            )

            if reset_status:
                return {
                    "status": "updated",
                    "message": f"Task {task.bid} updated and reset to pending",
                }
            else:
                return {
                    "status": "updated",
                    "message": f"Task {task.bid} context updated (status: {existing_status.value})",
                }

        # Create task in database
        await task_dal.create_bili_video_task(
            bvid=task.bid,
            favid=task.favid,
            task_context=task_context_dict,
        )
        return {"status": "success", "message": f"Task {task.bid} added to database"}

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
        "pending": stats[TaskStatus.PENDING.value],
        "executing": stats[TaskStatus.EXECUTING.value],
        "completed": stats[TaskStatus.COMPLETED.value],
        "failed": stats[TaskStatus.FAILED.value],
    }


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

    return result


@api_router.get("/tasks/{task_id}", tags=["任务"], summary="获取任务详情")
async def get_task_detail(task_id: int):
    """
    获取单个任务的详细信息。
    """
    task_dal = get_task_dal()

    async with task_dal.async_session() as session:
        from blsync.task_models import TaskModel, select

        stmt = select(TaskModel).where(TaskModel.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return task_dal._task_to_dict(task)


@api_router.put("/tasks/{task_id}/status", tags=["任务"], summary="手动修改任务状态")
async def update_task_status(task_id: int, request: UpdateTaskStatusRequest):
    """
    手动修改任务状态。

    支持的状态值：
    - pending: 待处理
    - executing: 执行中
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
        from blsync.task_models import TaskModel, select

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

        return task_dal._task_to_dict(task)


def start_server():
    import uvicorn

    from blsync.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000)
