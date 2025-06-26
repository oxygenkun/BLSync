import asyncio
import pathlib

import aiohttp


async def download_video(
    bvid,
    download_path,
    configs=None,
    extra_list_options=None,
):
    """
    使用 yutto 下载视频。

    :param media_id: 收藏夹的id
    :param bvid: 视频的bvid
    :param download_path: 存放视频的文件夹路径
    """
    video_url = f"https://www.bilibili.com/video/{bvid}"
    # fmt: off
    command = [
        "yutto",
        "-c", configs.credential.sessdata,
        "-d", download_path,
        "--no-danmaku",
        "--no-subtitle",
        "--with-metadata",
        "--save-cover",
        "--no-color",
        "--no-progress",
        video_url,
    ]
    # fmt: on
    if extra_list_options:
        command.extend(extra_list_options)

    print(f"start downloading {bvid}")

    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    if configs.verbose:
        while not proc.stdout.at_eof():
            if line := await proc.stdout.readline():
                print(line.decode().strip())
            if err := await proc.stderr.readline():
                print(err.decode().strip())
    else:
        _, stderr = await proc.communicate()
        if stderr:
            print(f"[stderr]\n{stderr.decode()}")
    # _, stderr = await proc.communicate()
    # if stderr:
    #     print(f"[stderr]\n{stderr.decode()}")

    print(f"end downloaded {bvid}")
    return True


async def download_file(url, download_path: pathlib.Path):
    """
    下载文件
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            download_path.write_bytes(await resp.read())
    print(f"Downloaded {url} to {download_path}")
    return True
