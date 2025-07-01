"""
Bilibili消费者模块 - 处理Bilibili相关的下载任务
"""
import asyncio
import dataclasses
import pathlib

import aiohttp
from loguru import logger
from yutto.processor.path_resolver import repair_filename

from .. import global_configs
from ..db_access import already_download_bvids, already_download_bvids_add
from ..postprocessor import PostProcessor
from ..scraper import BScraper
from .base import TaskContext


@dataclasses.dataclass
class BiliVideoTaskContext(TaskContext):
    """Bilibili视频下载任务"""
    bid: str
    favid: str

    def get_task_key(self) -> tuple:
        return (self.bid, self.favid)

    async def execute(self) -> None:
        """Execute video download task"""
        bid = self.bid
        favid = self.favid

        if bid in already_download_bvids(media_id=favid, configs=global_configs):
            logger.info(f"Already downloaded {bid}")
            return

        # 获取下载路径，支持简单和复杂配置格式
        fav_config = global_configs.favorite_list[favid]
        if isinstance(fav_config, str):
            # 简单格式: fid = "path"
            fav_download_path = pathlib.Path(fav_config)
        elif isinstance(fav_config, dict):
            # 复杂格式: 包含path字段
            fav_download_path = pathlib.Path(fav_config.get("path", ""))
        else:
            logger.info(f"Invalid favorite_list config for {favid}")
            return

        # 获取视频信息
        bs = BScraper(global_configs)
        v_info = await bs.get_video_info(bid)
        if v_info is None:
            logger.info(f"Failed to get video info for {bid}")
            return

        # 检查是否为多分P视频
        is_batch = v_info.get("videos", 1) > 1
        if is_batch:
            logger.info(f"Video {bid} has {v_info['videos']} parts, using batch mode")

        cover_path = pathlib.Path(
            fav_download_path, repair_filename(f"{v_info['title']}.jpg")
        )

        await asyncio.gather(
            download_video(
                bvid=bid,
                download_path=fav_download_path,
                configs=self.config,
                is_batch=is_batch,
            ),
            download_file(v_info["pic"], cover_path),
        )

        already_download_bvids_add(
            media_id=favid,
            bvid=bid,
            configs=global_configs,
        )

        # 执行下载后处理
        post_processor = PostProcessor(global_configs)
        postprocess_actions = post_processor.get_postprocess_actions(favid)
        if postprocess_actions:
            logger.info(
                f"Executing postprocess actions for {bid}: {postprocess_actions}"
            )
            await post_processor.execute_postprocess_actions(
                bid, favid, postprocess_actions
            )


async def download_video(
    bvid,
    download_path,
    configs=None,
    extra_list_options=None,
    is_batch=False,
):
    """
    使用 yutto 下载视频。

    :param media_id: 收藏夹的id
    :param bvid: 视频的bvid
    :param download_path: 存放视频的文件夹路径
    :param is_batch: 是否为多分P视频，若为True则添加--batch参数
    """
    video_url = f"https://www.bilibili.com/video/{bvid}"
    # fmt: off
    command = [
        "yutto",
        "-c", configs.credential.sessdata,
        "-d", str(download_path),
        "--no-danmaku",
        "--no-subtitle",
        "--with-metadata",
        "--save-cover",
        "--no-color",
        "--no-progress",
        video_url,
    ]
    # fmt: on

    # 如果是多分P视频，添加--batch参数
    if is_batch:
        command.insert(-1, "--batch")
        logger.info(f"Added --batch parameter for multi-part video {bvid}")

    if extra_list_options:
        command.extend(extra_list_options)

    logger.info(f"start downloading {bvid} with command: {' '.join(command)}")

    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    if configs.verbose:
        while not proc.stdout.at_eof():
            if line := await proc.stdout.readline():
                logger.info(line.decode().strip())
            if err := await proc.stderr.readline():
                logger.info(err.decode().strip())
    else:
        _, stderr = await proc.communicate()
        if stderr:
            logger.info(f"[stderr]\n{stderr.decode()}")

    logger.info(f"end downloaded {bvid}")
    return True


async def download_file(url, download_path: pathlib.Path):
    """
    下载文件
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            download_path.write_bytes(await resp.read())
    logger.info(f"Downloaded {url} to {download_path}")
    return True
