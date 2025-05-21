import asyncio
import dataclasses
import pathlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import global_configs
from .configs import Config
from .db_access import already_download_bvids, already_download_bvids_add
from .downloader import download_video
from .scraper import BScraper

task_queue = asyncio.Queue()


@dataclasses.dataclass
class TaskContext:
    config: Config


@dataclasses.dataclass
class BiliVideoTaskContext(TaskContext):
    bid: str
    favid: str


class TaskRequest(BaseModel):
    bid: str
    favid: str = -1  # 默认值为-1表示没有收藏夹id


async def task_consumer():
    """
    处理下载任务队列
    """
    while True:
        task_context = await task_queue.get()

        if task_context is None:
            break

        if isinstance(task_context, BiliVideoTaskContext):
            if task_context.bid in already_download_bvids(
                media_id=task_context.favid, configs=global_configs
            ):
                print(f"Already downloaded {task_context.bid}")
            else:
                fav_download_path = pathlib.Path(
                    global_configs.favorite_list[task_context.favid]
                )
                await download_video(
                    bvid=task_context.bid,
                    download_path=fav_download_path,
                    configs=task_context.config,
                )
                already_download_bvids_add(
                    media_id=task_context.favid,
                    bvid=task_context.bid,
                    configs=global_configs,
                )

        task_queue.task_done()
        print(f"[task_executor] queue has {task_queue.qsize()} tasks")


async def task_producer():
    """
    定时获取
    """
    bs = BScraper(global_configs)
    while True:
        async for bvid, favid in bs.get_all_bvids():
            await task_queue.put(
                BiliVideoTaskContext(config=global_configs, bid=bvid, favid=favid)
            )
            print(f"[generator] queue has {task_queue.qsize()} tasks")

        print(f"[generator] Sleeping for a while: {global_configs.interval} seconds")
        await asyncio.sleep(global_configs.interval)


async def start_background_tasks():
    """
    启动任务队列
    """
    task1 = asyncio.create_task(task_producer())
    task2 = asyncio.create_task(task_consumer())
    await asyncio.gather(task1, task2)


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


@app.post("/tasks/")
async def create_task(task: TaskRequest):
    try:
        await task_queue.put(
            BiliVideoTaskContext(config=global_configs, bid=task.bid, favid=task.favid)
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def start_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    start_server()
