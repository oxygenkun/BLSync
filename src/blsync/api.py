"""
FastAPI routes and request handlers.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from blsync.consumer.bilibili import BiliVideoTaskContext
from blsync.database import get_task_dal
from blsync.task_models import TaskStatus

BASE_DIR = Path(__file__).parents[2]
STATIC_DIR = BASE_DIR / "static"

router = APIRouter()


class TaskRequest(BaseModel):
    bid: str
    favid: str = "-1"  # 默认值为-1表示没有收藏夹id


@router.get("/", tags=["前端"], summary="前端页面")
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


@router.post("/task/bili", tags=["任务"], summary="创建 Bilibili 下载任务")
async def create_task(task: TaskRequest):
    """
    创建 Bilibili 视频下载任务

    任务创建逻辑：
    1. 检查数据库中是否已存在该任务
    2. 检查视频是否已下载
    3. 创建新任务到数据库
    """
    try:
        task_dal = get_task_dal()

        # 创建任务上下文
        task_context = BiliVideoTaskContext(bid=task.bid, task_name=task.favid)

        # Check if task already exists
        if await task_dal.has_bili_video_task(task.bid, task.favid):
            return {
                "status": "already_queued",
                "message": f"Task {task.bid} is already in database",
            }

        # Check if already downloaded (check for completed tasks)
        completed_bvids = await task_dal.get_completed_bvids(task.favid)
        if task.bid in completed_bvids:
            return {
                "status": "already_downloaded",
                "message": f"Video {task.bid} is already downloaded",
            }

        # Create task in database
        await task_dal.create_bili_video_task(
            bvid=task.bid,
            favid=task.favid,
            task_context=task_context.model_dump(),
        )
        return {"status": "success", "message": f"Task {task.bid} added to database"}

    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/status", tags=["任务"], summary="获取任务队列状态")
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


def start_server():
    import uvicorn

    from blsync.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000)
