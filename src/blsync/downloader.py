import asyncio
import pathlib

import aiohttp
from loguru import logger


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
    # _, stderr = await proc.communicate()
    # if stderr:
    #     print(f"[stderr]\n{stderr.decode()}")

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
