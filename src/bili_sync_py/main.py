import asyncio
import dataclasses
import pathlib

# from fastapi import FastAPI
import random

from .configs import Config, load_configs
from .db_access import already_download_bvids, already_download_bvids_add
from .downloader import download_video
from .scraper import BScraper

# app = FastAPI()

task_queue = asyncio.Queue()
configs = load_configs()


@dataclasses.dataclass
class TaskContext:
    config: Config


@dataclasses.dataclass
class BiliVideoTaskContext(TaskContext):
    bid: str
    favid: str


async def task(task_context):
    sleep_time = random.uniform(1, 10)
    print(f"[comsumer] task {task_context} started for {sleep_time} seconds")
    await asyncio.sleep(sleep_time)
    print(f"[comsumer] task {task_context} done")


async def task_executor():
    while True:
        task_context = await task_queue.get()

        if task_context is None:
            break

        if isinstance(task_context, BiliVideoTaskContext):
            if task_context.bid in already_download_bvids(
                media_id=task_context.favid, configs=configs
            ):
                print(f"Already downloaded {task_context.bid}")
            else:
                fav_downlaod_path = pathlib.Path(
                    configs.favorite_list[task_context.favid]
                )
                await download_video(
                    bvid=task_context.bid,
                    download_path=fav_downlaod_path,
                    configs=task_context.config,
                )
                already_download_bvids_add(
                    media_id=task_context.favid, bvid=task_context.bid, configs=configs
                )

        task_queue.task_done()
        print(f"[task_executor] queue has {task_queue.qsize()} tasks")


async def periodic_task_generator():
    bs = BScraper(configs)
    while True:
        async for bvid, favid in bs.get_all_bvids():
            await task_queue.put(
                BiliVideoTaskContext(config=configs, bid=bvid, favid=favid)
            )
            print(f"[generator] queue has {task_queue.qsize()} tasks")

        print(f"[generator] Sleeping for a while: {configs.interval} seconds")
        await asyncio.sleep(configs.interval)


async def start():
    await asyncio.gather(task_executor(), periodic_task_generator())


def main():
    asyncio.run(start())


if __name__ == "__main__":
    main()
