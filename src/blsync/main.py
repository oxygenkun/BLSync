import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from blsync import get_global_configs
from blsync.api import router
from blsync.consumer.base import Task
from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.database import get_semaphore, get_task_dal
from blsync.scraper import BScraper
from blsync.task_models import (
    TaskStatus,
    make_bili_video_key,
    parse_bili_video_key,
)


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
                # Mark task as executing immediately when scheduled
                await task_dal.update_task_status(task_model.task_key, TaskStatus.EXECUTING)

                # Deserialize task context and create task instance
                task_context_dict = task_model.task_context_dict
                context = BiliVideoTaskContext(**task_context_dict)
                task = BiliVideoTask(context)

                # Create async task for execution (non-blocking)
                # Pass task_key_str for database updates
                asyncio.create_task(process_single_task(task, task_model.task_key))
                bvid, _ = parse_bili_video_key(task_model.task_key)
                logger.info(
                    f"[task_consumer] Scheduled task {bvid}, "
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
    1. 检查任务是否在表中
    2. 如果不在，添加任务（PENDING）
    3. 如果在表中：
       - PENDING/EXECUTING/COMPLETED：跳过
       - FAILED：更新为 PENDING（重试）
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
                    logger.info(f"[task_producer] Added new task {bvid} for {task_name}")
                elif status == TaskStatus.FAILED:
                    # 任务失败，重置为 PENDING 以重试
                    task_key = make_bili_video_key(bvid, task_name)
                    await task_dal.update_task_status(task_key, TaskStatus.PENDING)
                    logger.info(f"[task_producer] Reset failed task {bvid} for {task_name} to PENDING")
                elif status in (TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.COMPLETED):
                    # 任务正在处理、执行中或已完成，跳过
                    logger.debug(
                        f"[task_producer] Task {bvid} (task_name: {task_name}) "
                        f"is {status.value}, skipping"
                    )
                else:
                    logger.warning(f"[task_producer] Unknown task status: {status}")

            logger.info(f"[task_producer] Sleeping for {config.interval} seconds")
            await asyncio.sleep(config.interval)

        except Exception as e:
            logger.error(f"Error in task_producer: {e}")
            await asyncio.sleep(config.interval)


async def delete_stale_tasks():
    """
    定期清理已完成但仍在数据库中的任务

    清理逻辑：
    1. 检查 pending 和 executing 状态的任务
    2. 如果视频已下载，删除任务记录

    NOTE: Currently disabled - using completed task status for deduplication
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
    启动Web服务前，启动后台任务
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

# 注册路由
app.include_router(router)


def main():
    """启动FastAPI应用的主入口"""
    import uvicorn

    uvicorn.run(
        "src.blsync.main:app",
        host="0.0.0.0",
        port=8000,
        # reload=True,
    )


if __name__ == "__main__":
    main()
