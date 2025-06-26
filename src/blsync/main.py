import asyncio
import dataclasses
import pathlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel
from yutto.processor.path_resolver import repair_filename

from . import global_configs
from .configs import Config
from .db_access import already_download_bvids, already_download_bvids_add
from .downloader import download_file, download_video
from .postprocessor import PostProcessor
from .scraper import BScraper

task_queue = asyncio.Queue()
bs = BScraper(global_configs)
post_processor = PostProcessor(global_configs)


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
            bid = task_context.bid
            if bid in already_download_bvids(
                media_id=task_context.favid, configs=global_configs
            ):
                logger.info(f"Already downloaded {bid}")
            else:
                # 获取下载路径，支持简单和复杂配置格式
                fav_config = global_configs.favorite_list[task_context.favid]
                if isinstance(fav_config, str):
                    # 简单格式: fid = "path"
                    fav_download_path = pathlib.Path(fav_config)
                elif isinstance(fav_config, dict):
                    # 复杂格式: 包含path字段
                    fav_download_path = pathlib.Path(fav_config.get("path", ""))
                else:
                    logger.info(
                        f"Invalid favorite_list config for {task_context.favid}"
                    )
                    continue

                v_info = await bs.get_video_info(bid)
                if v_info is None:
                    logger.info(f"Failed to get video info for {bid}")
                    continue

                # 检查是否为多分P视频
                is_batch = v_info.get("videos", 1) > 1
                if is_batch:
                    logger.info(
                        f"Video {bid} has {v_info['videos']} parts, using batch mode"
                    )

                cover_path = pathlib.Path(
                    fav_download_path, repair_filename(f"{v_info['title']}.jpg")
                )

                await asyncio.gather(
                    download_video(
                        bvid=bid,
                        download_path=fav_download_path,
                        configs=task_context.config,
                        is_batch=is_batch,
                    ),
                    download_file(v_info["pic"], cover_path),
                )

                already_download_bvids_add(
                    media_id=task_context.favid,
                    bvid=bid,
                    configs=global_configs,
                )

            # 执行下载后处理
            postprocess_actions = post_processor.get_postprocess_actions(
                task_context.favid
            )
            if postprocess_actions:
                logger.info(
                    f"Executing postprocess actions for {bid}: {postprocess_actions}"
                )
                await post_processor.execute_postprocess_actions(
                    bid, task_context.favid, postprocess_actions
                )

        task_queue.task_done()
        logger.info(f"[task_executor] queue has {task_queue.qsize()} tasks")


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
            logger.info(f"[generator] queue has {task_queue.qsize()} tasks")

        logger.info(
            f"[generator] Sleeping for a while: {global_configs.interval} seconds"
        )
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
    # start_server()
    asyncio.run(start_background_tasks())
