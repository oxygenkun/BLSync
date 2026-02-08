import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from blsync import get_global_configs
from blsync.consumer.base import Task
from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.db_access import already_download_bvids
from blsync.scraper import BScraper
from blsync.task_models import (
    TaskDAL,
    TaskStatus,
    parse_bili_video_key,
)

# Global task database access layer
_task_dal: TaskDAL | None = None

# 创建信号量来控制并发任务数
_semaphore = None


def get_task_dal() -> TaskDAL:
    """Get the global task database access layer."""
    global _task_dal
    if _task_dal is None:
        config = get_global_configs()
        db_path = config.data_path
        # Convert pathlib Path to string and ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_path}"
        _task_dal = TaskDAL(db_url)
    return _task_dal


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_global_configs().max_concurrent_tasks)
    return _semaphore


def get_scraper():
    return BScraper(get_global_configs())


async def process_single_task(task: Task, task_key_str: str):
    """
    处理单个任务

    Args:
        task: Task instance to execute
        task_key_str: Task key JSON string for database updates
    """
    config = get_global_configs()
    task_dal = get_task_dal()
    bvid, favid = parse_bili_video_key(task_key_str)

    async with get_semaphore():  # 限制并发数
        try:
            # Update status to executing
            await task_dal.update_task_status(task_key_str, TaskStatus.EXECUTING)

            # 添加超时控制
            await asyncio.wait_for(task.execute(), timeout=config.task_timeout)
            logger.info(f"Task {(bvid, favid)} completed successfully")

            # Update status to completed
            await task_dal.update_task_status(task_key_str, TaskStatus.COMPLETED)

        except asyncio.TimeoutError:
            error_msg = f"Task {(bvid, favid)} timed out after {config.task_timeout}s"
            logger.exception(error_msg)
            await task_dal.update_task_status(
                task_key_str, TaskStatus.FAILED, error_msg
            )
        except Exception as e:
            error_msg = f"Error processing task {(bvid, favid)}: {e}"
            logger.exception(error_msg)
            await task_dal.update_task_status(
                task_key_str, TaskStatus.FAILED, error_msg
            )


async def task_consumer():
    """
    处理下载任务 - 从数据库获取待处理任务

    使用数据库统一管理任务状态：
    - pending: 等待执行
    - executing: 正在执行
    - completed: 已完成
    - failed: 执行失败
    """
    task_dal = get_task_dal()

    while True:
        try:
            # Get pending tasks from database
            pending_tasks = await task_dal.get_pending_tasks()

            if not pending_tasks:
                await asyncio.sleep(1)
                continue

            # Process pending tasks
            for task_model in pending_tasks:
                # Check if we can start a new task (respect semaphore)
                if get_semaphore()._value <= 0:
                    break

                # Deserialize task context and create task instance
                task_context_dict = task_model.task_context_dict
                # Remove 'config' key if it exists (for backward compatibility)
                task_context_dict.pop("config", None)
                context = BiliVideoTaskContext(**task_context_dict)
                task = BiliVideoTask(context)

                # Create async task for execution (non-blocking)
                # Pass task_key_str for database updates
                asyncio.create_task(process_single_task(task, task_model.task_key))
                bvid, _ = parse_bili_video_key(task_model.task_key)
                logger.info(
                    f"[task_consumer] Started task {bvid}, "
                    f"{len(pending_tasks)} pending tasks remaining"
                )

            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error in task_consumer: {e}")
            await asyncio.sleep(5)


async def task_producer():
    """
    定时获取收藏夹视频并创建任务

    任务去重逻辑：
    1. 检查数据库中是否已存在该任务
    2. 检查视频是否已下载
    """
    logger.info("[task_producer] Starting task producer")
    config = get_global_configs()
    bs = get_scraper()
    task_dal = get_task_dal()

    while True:
        try:
            async for bvid, task_name in bs.get_all_bvids():
                # 创建任务上下文
                context = BiliVideoTaskContext(bid=bvid, task_name=task_name)

                # Check if task already exists in database
                if await task_dal.bili_video_task_exists(bvid, task_name):
                    logger.debug(
                        f"Task {bvid} (task_name: {task_name}) already exists, skipping"
                    )
                    continue

                # Check if already downloaded
                if bvid in already_download_bvids(media_id=task_name, configs=config):
                    logger.debug(f"Video {bvid} already downloaded, skipping")
                    continue

                # Create task in database
                await task_dal.create_bili_video_task(
                    bvid=bvid,
                    favid=task_name,
                    task_context=context.model_dump(),
                )
                logger.info(f"[task_producer] Added new task {bvid} for {task_name}")

            logger.info(f"[task_producer] Sleeping for {config.interval} seconds")
            await asyncio.sleep(config.interval)

        except Exception as e:
            logger.error(f"Error in task_producer: {e}")
            await asyncio.sleep(config.interval)


async def cleanup_stale_tasks():
    """
    定期清理已完成但仍在数据库中的任务

    清理逻辑：
    1. 检查 pending 和 executing 状态的任务
    2. 如果视频已下载，删除任务记录
    """
    while True:
        try:
            await asyncio.sleep(300)  # 每5分钟检查一次

            config = get_global_configs()
            task_dal = get_task_dal()

            # Check each favorite list
            for favid in config.favorite_list.keys():
                downloaded_bvids = already_download_bvids(
                    media_id=favid, configs=config
                )

                # Clean up stale tasks
                deleted_keys = await task_dal.cleanup_stale_tasks(
                    downloaded_bvids=downloaded_bvids, favid=favid
                )

                for bvid, _ in deleted_keys:
                    logger.info(f"Cleaned up stale task: {bvid} in {favid}")

        except Exception as e:
            logger.error(f"Error in cleanup_stale_tasks: {e}")


async def start_background_tasks():
    """
    启动后台任务

    启动三个后台协程：
    1. task_producer: 定期获取收藏夹视频并创建任务
    2. task_consumer: 从数据库获取待处理任务并执行
    3. cleanup_stale_tasks: 定期清理已完成任务
    """
    # Initialize database tables
    task_dal = get_task_dal()
    await task_dal.create_tables()

    task1 = asyncio.create_task(task_producer())
    task2 = asyncio.create_task(task_consumer())
    task3 = asyncio.create_task(cleanup_stale_tasks())
    await asyncio.gather(task1, task2, task3)


###################
# FastAPI 相关代码 #
###################


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    启动Web服务前，启动后台任务
    """
    logger.info("Starting background tasks...")
    tasks = asyncio.create_task(start_background_tasks())
    yield
    tasks.cancel()
    # Close database connection
    if _task_dal:
        await _task_dal.close()


app = FastAPI(lifespan=lifespan)

BASE_DIR = Path(__file__).parents[2]
STATIC_DIR = BASE_DIR / "static"


@app.get("/", tags=["前端"], summary="前端页面")
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


class TaskRequest(BaseModel):
    bid: str
    favid: str = "-1"  # 默认值为-1表示没有收藏夹id


@app.post("/task/bili", tags=["任务"], summary="创建 Bilibili 下载任务")
async def create_task(task: TaskRequest):
    """
    创建 Bilibili 视频下载任务

    任务创建逻辑：
    1. 检查数据库中是否已存在该任务
    2. 检查视频是否已下载
    3. 创建新任务到数据库
    """
    try:
        config = get_global_configs()
        task_dal = get_task_dal()

        # 创建任务上下文
        task_context = BiliVideoTaskContext(bid=task.bid, task_name=task.favid)

        # Check if task already exists
        if await task_dal.bili_video_task_exists(task.bid, task.favid):
            return {
                "status": "already_queued",
                "message": f"Task {task.bid} is already in database",
            }

        # Check if already downloaded
        if task.bid in already_download_bvids(media_id=task.favid, configs=config):
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


@app.get("/tasks/status", tags=["任务"], summary="获取任务队列状态")
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
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    start_server()
