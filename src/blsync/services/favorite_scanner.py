"""Favorite-list scanning use case shared by API and background jobs."""

import asyncio

from loguru import logger

from blsync.configuration.store import get_config
from blsync.consumer.bilibili import BiliVideoTaskContext
from blsync.db import get_task_dal
from blsync.db.task import TaskStatus, make_bili_video_key
from blsync.scraper import BScraper

_scan_lock = asyncio.Lock()


async def scan_favorites_once() -> dict[str, int]:
    """Scan configured favorites once and enqueue missing or failed tasks."""
    async with _scan_lock:
        config = get_config()
        scraper = BScraper(config)
        task_dal = get_task_dal()
        stats = {"created": 0, "reset": 0, "skipped": 0}

        async for bvid, task_name in scraper.get_all_bvids():
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
