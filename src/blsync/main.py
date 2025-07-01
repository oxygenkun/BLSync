import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

from . import global_configs
from .consumer.bilibili import BiliVideoTaskContext
from .db_access import already_download_bvids
from .scraper import BScraper

task_queue = asyncio.Queue()
# Track tasks that are currently queued or being processed
queued_tasks = set()  # Set of (bvid, favid) tuples
processing_tasks = set()  # Set of (bvid, favid) tuples currently being processed
bs = BScraper(global_configs)


async def task_consumer():
    """
    处理下载任务队列

    queued_tasks --- Set of tasks that are currently queued
    processing_tasks --- Set of tasks that are currently being processed

    """
    while True:
        task_context = await task_queue.get()

        if task_context is None:
            break

        task_key = task_context.get_task_key()

        queued_tasks.discard(task_key)
        processing_tasks.add(task_key)

        try:
            await task_context.execute()
        except Exception as e:
            logger.error(f"Error processing task {task_key}: {e}")
        finally:
            processing_tasks.discard(task_key)

        task_queue.task_done()
        logger.info(f"[task_executor] queue has {task_queue.qsize()} tasks")


async def task_producer():
    """
    定时获取
    """
    bs = BScraper(global_configs)
    while True:
        async for bvid, favid in bs.get_all_bvids():
            # 创建任务实例
            task = BiliVideoTaskContext(config=global_configs, bid=bvid, favid=favid)
            task_key = task.get_task_key()

            # Check if task is already queued or being processed
            if task_key in queued_tasks or task_key in processing_tasks:
                logger.debug(
                    f"Task {bvid} (favid: {favid}) already queued or processing, skipping"
                )
                continue

            # Check if already downloaded
            if bvid in already_download_bvids(media_id=favid, configs=global_configs):
                logger.debug(f"Video {bvid} already downloaded, skipping")
                continue

            # Add to queued tasks and put in queue
            queued_tasks.add(task_key)
            await task_queue.put(task)
            logger.info(
                f"[generator] Added new task {bvid}, queue has {task_queue.qsize()} tasks"
            )

        logger.info(
            f"[generator] Sleeping for a while: {global_configs.interval} seconds"
        )
        await asyncio.sleep(global_configs.interval)


async def cleanup_stale_tasks():
    """
    定期清理可能卡住的任务
    """
    while True:
        await asyncio.sleep(300)  # 每5分钟检查一次

        # 清理已下载但仍在追踪集合中的任务
        stale_tasks = []
        for task_key in queued_tasks.union(processing_tasks):
            # 处理不同类型的任务键
            if len(task_key) == 2:  # (bvid, favid) 格式
                bvid, favid = task_key
                if bvid in already_download_bvids(
                    media_id=favid, configs=global_configs
                ):
                    stale_tasks.append(task_key)
            # 其他类型的任务键可以在这里添加处理逻辑

        for task_key in stale_tasks:
            queued_tasks.discard(task_key)
            processing_tasks.discard(task_key)
            logger.info(f"Cleaned up stale task: {task_key}")


async def start_background_tasks():
    """
    启动任务队列
    """
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
    tasks = asyncio.create_task(start_background_tasks())
    yield
    tasks.cancel()


app = FastAPI(lifespan=lifespan)


class TaskRequest(BaseModel):
    bid: str
    favid: str = -1  # 默认值为-1表示没有收藏夹id


@app.post("/tasks/")
async def create_task(task: TaskRequest):
    try:
        # 创建任务实例
        task_instance = BiliVideoTaskContext(
            config=global_configs, bid=task.bid, favid=task.favid
        )
        task_key = task_instance.get_task_key()

        # Check if task is already queued or being processed
        if task_key in queued_tasks or task_key in processing_tasks:
            return {
                "status": "already_queued",
                "message": f"Task {task.bid} is already queued or being processed",
            }

        # Check if already downloaded
        if task.bid in already_download_bvids(
            media_id=task.favid, configs=global_configs
        ):
            return {
                "status": "already_downloaded",
                "message": f"Video {task.bid} is already downloaded",
            }

        # Add to queued tasks and put in queue
        queued_tasks.add(task_key)
        await task_queue.put(task_instance)
        return {"status": "success", "message": f"Task {task.bid} added to queue"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks/status")
async def get_task_status():
    """
    获取任务队列状态
    """
    return {
        "queue_size": task_queue.qsize(),
        "queued_tasks_count": len(queued_tasks),
        "processing_tasks_count": len(processing_tasks),
        "queued_tasks": list(queued_tasks),
        "processing_tasks": list(processing_tasks),
    }


def start_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    # start_server()
    asyncio.run(start_background_tasks())

# 使用示例（在main.py中可以这样导入和使用不同的任务类型）:
#
# from .consumer.bilibili import BiliVideoTaskContext
# from .consumer.audio import AudioTaskContext
# from .consumer.playlist import PlaylistTaskContext
#
# # 创建不同类型的任务：
# video_task = BiliVideoTaskContext(config=config, bid="BV123", favid="456")
# audio_task = AudioTaskContext(config=config, bid="BV123", favid="456", audio_quality="320k")
# playlist_task = PlaylistTaskContext(config=config, playlist_id="PL123")
