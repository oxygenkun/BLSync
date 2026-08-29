import asyncio
import contextlib
import os
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from blsync.api import api_router, file_router, frontend_router
from blsync.configuration.store import get_config
from blsync.consumer.base import Task
from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.database import get_task_dal
from blsync.model.task import (
    TaskStatus,
    make_bili_video_key,
    parse_bili_video_key,
)
from blsync.progress import (
    DownloadProgressEvent,
    ProgressEventType,
    get_progress_broker,
)
from blsync.routes.config import router as config_router
from blsync.scraper import BScraper

_scan_lock = asyncio.Lock()
WORKER_ID = os.environ.get(
    "WORKER_ID", os.environ.get("HOSTNAME", socket.gethostname())
)
CONTROL_POLL_SECONDS = 1.0
LEASE_SECONDS = 30.0


def setup_logger():
    """配置 logger，从配置文件读取日志级别"""
    config = get_config()
    logger.remove()
    logger.add(sys.stderr, level=config.log_level)


def get_scraper():
    return BScraper(get_config())


async def _monitor_task_control(
    task_id: int,
    execution: asyncio.Task[None],
    pause_detected: asyncio.Event,
) -> None:
    """Renew ownership and stop local work when DB requests a pause."""
    task_dal = get_task_dal()
    while not execution.done():
        await asyncio.sleep(CONTROL_POLL_SECONDS)
        action = await task_dal.renew_lease(task_id, WORKER_ID, LEASE_SECONDS)
        if action is None or action == "pause":
            if action == "pause":
                pause_detected.set()
            execution.cancel()
            return


async def process_single_task(task: Task, task_key_str: str):
    """
    处理单个任务

    Args:
        task: Task instance to execute
        task_key_str: Task key JSON string for database updates
    """
    config = get_config()
    task_dal = get_task_dal()
    bvid, favid = parse_bili_video_key(task_key_str)
    task_id = task._task_context.task_id if isinstance(task, BiliVideoTask) else None
    if task_id is None:
        raise ValueError("A persisted task id is required for DB-controlled execution")

    # task_consumer tracks running_tasks and applies the live concurrency limit.
    execution: asyncio.Task[None] | None = None
    monitor: asyncio.Task[None] | None = None
    pause_detected = asyncio.Event()
    try:
        action = await task_dal.renew_lease(task_id, WORKER_ID, LEASE_SECONDS)
        if action != "run":
            await task_dal.update_owned_task_status(
                task_id, WORKER_ID, TaskStatus.PAUSED, release=True
            )
            return

        owned = await task_dal.update_owned_task_status(
            task_id, WORKER_ID, TaskStatus.DOWNLOADING
        )
        if owned is None:
            return
        if isinstance(task, BiliVideoTask) and task._task_context.task_id is not None:
            get_progress_broker().publish(
                task._task_context.task_id,
                DownloadProgressEvent(
                    event=ProgressEventType.STATUS,
                    task_id=task._task_context.task_id,
                    bvid=bvid,
                    status=TaskStatus.DOWNLOADING.value,
                ),
            )

        # 添加超时控制
        execution = asyncio.create_task(task.execute())
        monitor = asyncio.create_task(
            _monitor_task_control(task_id, execution, pause_detected)
        )
        await asyncio.wait_for(execution, timeout=config.task_timeout)
        if isinstance(task, BiliVideoTask):
            await task_dal.update_owned_task_downloaded_files(
                task_id,
                WORKER_ID,
                [str(path) for path in task.downloaded_files],
            )
        logger.info(f"Task {(bvid, favid)} completed successfully")

        # Update status to done
        await task_dal.update_owned_task_status(
            task_id, WORKER_ID, TaskStatus.COMPLETED, release=True
        )

    except asyncio.CancelledError:
        if pause_detected.is_set():
            logger.info(f"Task {(bvid, favid)} paused by user request")
            await task_dal.update_owned_task_status(
                task_id, WORKER_ID, TaskStatus.PAUSED, release=True
            )
            return
        if execution is not None:
            execution.cancel()
        raise
    except TimeoutError:
        error_msg = f"Task {(bvid, favid)} timed out after {config.task_timeout}s"
        logger.exception(error_msg)
        await task_dal.update_owned_task_status(
            task_id, WORKER_ID, TaskStatus.FAILED, error_msg, release=True
        )
    except Exception as e:
        error_msg = f"Error processing task {(bvid, favid)}: {e}"
        logger.exception(error_msg)
        await task_dal.update_owned_task_status(
            task_id, WORKER_ID, TaskStatus.FAILED, error_msg, release=True
        )
        if isinstance(task, BiliVideoTask) and task._task_context.task_id is not None:
            get_progress_broker().publish(
                task._task_context.task_id,
                DownloadProgressEvent(
                    event=ProgressEventType.FAILED,
                    task_id=task._task_context.task_id,
                    bvid=bvid,
                    status=TaskStatus.FAILED.value,
                    message=error_msg,
                ),
            )
    finally:
        if monitor is not None:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor


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
    running_tasks: set[asyncio.Task[None]] = set()

    while True:
        try:
            # TODO(config-observer): subscribe to config_store.on_change(
            # "max_concurrent_tasks") instead of polling every loop iteration.
            config = get_config()
            available_slots = config.max_concurrent_tasks - len(running_tasks)
            if available_slots <= 0:
                await asyncio.sleep(0.2)
                continue

            recovered = await task_dal.recover_expired_tasks()
            if recovered:
                logger.warning(f"Recovered {recovered} tasks with expired leases")
            ready_tasks = await task_dal.claim_ready_tasks(
                WORKER_ID, available_slots, LEASE_SECONDS
            )

            if not ready_tasks:
                await asyncio.sleep(1)
                continue

            # Process ready tasks
            for task_model in ready_tasks:
                try:
                    # Deserialize task context and create task instance
                    task_context_dict = task_model.task_context_dict
                    context = BiliVideoTaskContext(
                        **{
                            **task_context_dict,
                            "task_id": task_model.id,
                        }
                    )
                    task = BiliVideoTask(context)

                    # Create async task for execution (non-blocking)
                    # Pass task_key_str for database updates
                    running_task = asyncio.create_task(
                        process_single_task(task, task_model.task_key)
                    )
                    running_tasks.add(running_task)
                    running_task.add_done_callback(running_tasks.discard)
                    logger.info(
                        f"[task_consumer] Scheduled task {task_model.task_key}, "
                        f"{len(ready_tasks)} ready tasks remaining"
                    )
                except Exception as e:
                    error_msg = f"Failed to create task for {task_model.task_key}: {e}"
                    logger.exception(error_msg)
                    await task_dal.update_owned_task_status(
                        task_model.id,
                        WORKER_ID,
                        TaskStatus.FAILED,
                        error_msg,
                        release=True,
                    )

            await asyncio.sleep(1)

        except asyncio.CancelledError:
            for running_task in running_tasks:
                running_task.cancel()
            await asyncio.gather(*running_tasks, return_exceptions=True)
            raise
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
       - FAILED：默认跳过；仅在 retry_failed_tasks 开启时更新为 READY
    """
    logger.info("[task_producer] Starting task producer")
    while True:
        try:
            # TODO(config-observer): subscribe to config_store.on_change(
            # "interval") instead of polling every loop iteration.
            config = get_config()
            await scan_favorites_once()

            logger.debug(f"[task_producer] Sleeping for {config.interval} seconds")
            await asyncio.sleep(config.interval)

        except Exception as e:
            logger.error(f"Error in task_producer: {e}")
            await asyncio.sleep(config.interval)


async def scan_favorites_once() -> dict[str, int]:
    """Scan configured favorites once and enqueue missing or failed tasks."""
    async with _scan_lock:
        config = get_config()
        bs = get_scraper()
        task_dal = get_task_dal()
        stats = {"created": 0, "reset": 0, "skipped": 0}

        async for bvid, task_name in bs.get_all_bvids():
            context = BiliVideoTaskContext(bid=bvid, task_name=task_name)
            status = await task_dal.get_bili_video_task_status(bvid, task_name)

            if status is None:
                await task_dal.create_bili_video_task(
                    bvid=bvid,
                    favid=task_name,
                    task_context=context.model_dump(),
                )
                stats["created"] += 1
                logger.info(f"[task_producer] Added new task {bvid} for {task_name}")
            elif status == TaskStatus.FAILED:
                if config.retry_failed_tasks:
                    task_key = make_bili_video_key(bvid, task_name)
                    await task_dal.update_task_status(task_key, TaskStatus.READY)
                    stats["reset"] += 1
                    logger.info(
                        f"[task_producer] Reset failed task {bvid} "
                        f"for {task_name} to READY"
                    )
                else:
                    stats["skipped"] += 1
                    logger.debug(
                        f"[task_producer] Failed task {bvid} requires manual retry"
                    )
            elif status in (
                TaskStatus.READY,
                TaskStatus.CONSUMING,
                TaskStatus.DOWNLOADING,
                TaskStatus.PAUSING,
                TaskStatus.PAUSED,
                TaskStatus.COMPLETED,
            ):
                stats["skipped"] += 1
                logger.debug(
                    f"[task_producer] Task {bvid} (task_name: {task_name}) "
                    f"is {status.value}, skipping"
                )
            else:
                logger.warning(f"[task_producer] Unknown task status: {status}")

        return stats


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
            # config = get_config()
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
    task_dal = get_task_dal()
    version = await task_dal.migrate()
    logger.info(f"Database schema is at version {version}")
    logger.info("Starting background tasks...")
    tasks = asyncio.create_task(start_background_tasks())
    try:
        yield
    finally:
        tasks.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tasks
        await task_dal.release_worker_tasks(WORKER_ID)
        await task_dal.close()


app = FastAPI(lifespan=lifespan)

# 注册路由 - API 路由优先，避免被前端 catch-all 路由拦截
app.include_router(api_router, prefix="/api")  # API 路由 /api/*
app.include_router(config_router, prefix="/api")
app.include_router(file_router)  # 文件路由 /file/*
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
        reload=True,
    )


if __name__ == "__main__":
    main()
