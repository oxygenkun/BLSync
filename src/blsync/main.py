import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from blsync import get_global_configs
from blsync.api import api_router, frontend_router
from blsync.consumer.base import Task
from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.database import get_semaphore, get_task_dal
from blsync.model.task import (
    TaskStatus,
    make_bili_video_key,
    parse_bili_video_key,
)
from blsync.scraper import BScraper


def setup_logger():
    """配置 logger，从配置文件读取日志级别"""
    config = get_global_configs()
    logger.remove()
    logger.add(sys.stderr, level=config.log_level)


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
            # Update status to downloading before starting download
            await task_dal.update_task_status(task_key_str, TaskStatus.DOWNLOADING)

            # 添加超时控制
            await asyncio.wait_for(task.execute(), timeout=config.task_timeout)
            logger.info(f"Task {(bvid, favid)} completed successfully")

            # Update status to done
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
    - ready: 准备就绪但是 consumer 没有开始
    - consuming: consumer 开始了但是没有下载
    - downloading: 正在下载
    - done: 下载完成
    - failed: 执行失败
    """
    task_dal = get_task_dal()

    while True:
        try:
            # Get ready tasks from database
            ready_tasks = await task_dal.get_ready_tasks()

            if not ready_tasks:
                await asyncio.sleep(1)
                continue

            # Process ready tasks
            for task_model in ready_tasks:
                # Mark task as consuming immediately when scheduled
                await task_dal.update_task_status(
                    task_model.task_key, TaskStatus.CONSUMING
                )

                try:
                    # Deserialize task context and create task instance
                    task_context_dict = task_model.task_context_dict
                    context = BiliVideoTaskContext(**task_context_dict)
                    task = BiliVideoTask(context)

                    # Create async task for execution (non-blocking)
                    # Pass task_key_str for database updates
                    asyncio.create_task(process_single_task(task, task_model.task_key))
                    logger.info(
                        f"[task_consumer] Scheduled task {task_model.task_key}, "
                        f"{len(ready_tasks)} ready tasks remaining"
                    )
                except Exception as e:
                    error_msg = f"Failed to create task for {task_model.task_key}: {e}"
                    logger.exception(error_msg)
                    await task_dal.update_task_status(
                        task_model.task_key, TaskStatus.FAILED, error_msg
                    )

            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error in task_consumer: {e}")
            await asyncio.sleep(5)


async def task_producer():
    """
    定时获取收藏夹视频并创建任务

    任务去重逻辑：
    1. 检查任务是否在表中
    2. 如果不在，添加任务（READY）
    3. 如果在表中：
       - READY/CONSUMING/DOWNLOADING/DONE：跳过
       - FAILED：更新为 READY（重试）
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

                # 获取任务状态
                status = await task_dal.get_bili_video_task_status(bvid, task_name)

                if status is None:
                    # 任务不存在，创建新任务
                    await task_dal.create_bili_video_task(
                        bvid=bvid,
                        favid=task_name,
                        task_context=context.model_dump(),
                    )
                    logger.info(
                        f"[task_producer] Added new task {bvid} for {task_name}"
                    )
                elif status == TaskStatus.FAILED:
                    # 任务失败，重置为 READY 以重试
                    task_key = make_bili_video_key(bvid, task_name)
                    await task_dal.update_task_status(task_key, TaskStatus.READY)
                    logger.info(
                        f"[task_producer] Reset failed task {bvid} for {task_name} to READY"
                    )
                elif status in (
                    TaskStatus.READY,
                    TaskStatus.CONSUMING,
                    TaskStatus.DOWNLOADING,
                    TaskStatus.COMPLETED,
                ):
                    # 任务正在处理、下载中或已完成，跳过
                    logger.debug(
                        f"[task_producer] Task {bvid} (task_name: {task_name}) "
                        f"is {status.value}, skipping"
                    )
                else:
                    logger.warning(f"[task_producer] Unknown task status: {status}")

            logger.debug(f"[task_producer] Sleeping for {config.interval} seconds")
            await asyncio.sleep(config.interval)

        except Exception as e:
            logger.error(f"Error in task_producer: {e}")
            await asyncio.sleep(config.interval)


async def delete_stale_tasks():
    """
    定期清理已完成但仍在数据库中的任务

    清理逻辑：
    1. 检查 ready、consuming 和 downloading 状态的任务
    2. 如果视频已下载，删除任务记录

    NOTE: Currently disabled - using done task status for deduplication
    """
    while True:
        try:
            await asyncio.sleep(300)  # 每5分钟检查一次

            # NOTE: delete_stale_tasks is disabled - using completed task status instead
            # config = get_global_configs()
            # task_dal = get_task_dal()
            # for favid in config.favorite_list.keys():
            #     downloaded_bvids = await task_dal.get_completed_bvids(favid)
            #     deleted_keys = await task_dal.delete_stale_tasks(favid=favid)
            #     for bvid, _ in deleted_keys:
            #         logger.info(f"Deleted stale task: {bvid} in {favid}")

        except Exception as e:
            logger.error(f"Error in delete_stale_tasks: {e}")


async def start_background_tasks():
    """
    启动后台任务

    启动三个后台协程：
    1. task_producer: 定期获取收藏夹视频并创建任务
    2. task_consumer: 从数据库获取待处理任务并执行
    3. delete_stale_tasks: 定期清理已完成任务
    """
    # Initialize database tables
    task_dal = get_task_dal()
    await task_dal.create_tables()

    task1 = asyncio.create_task(task_producer())
    task2 = asyncio.create_task(task_consumer())
    task3 = asyncio.create_task(delete_stale_tasks())
    await asyncio.gather(task1, task2, task3)


###################
# FastAPI 应用配置 #
###################


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    启动Web服务前，启动后台任务ß
    """
    logger.info("Starting background tasks...")
    tasks = asyncio.create_task(start_background_tasks())
    yield
    tasks.cancel()
    # Close database connection
    task_dal = get_task_dal()
    if task_dal:
        await task_dal.close()


app = FastAPI(lifespan=lifespan)

# 注册路由 - API 路由优先，避免被前端 catch-all 路由拦截
app.include_router(api_router, prefix="/api")  # API 路由 /api/*
app.include_router(frontend_router)  # 根路由 / (前端页面)


def main():
    """启动FastAPI应用的主入口"""
    import uvicorn

    # 配置日志级别
    setup_logger()

    uvicorn.run(
        "blsync.main:app",
        host="0.0.0.0",
        port=8000,
        # reload=True,
    )


if __name__ == "__main__":
    main()
