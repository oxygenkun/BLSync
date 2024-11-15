import asyncio


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
    video_url = "https://www.bilibili.com/video/" + bvid  # 使用bvid拼接出视频的下载地址
    # command = [
    #     "yt-dlp", # 调用yt-dlp已经下载的视频会自动跳过
    #     "-f", "bestvideo+bestaudio/best",video_url, # 最高画质下载视频
    #     "--write-thumbnail", # 下载视频的缩略图或海报图片并保存为单独的文件
    #     # "--embed-thumbnail" # 先下载缩略图或海报图片，并将它嵌入到视频文件中（如果视频格式支持），需要ffmpeg
    #     "--external-downloader", "aria2c", # 启用aria2，将支持aria2的特性断点续传和多线程
    #     "--external-downloader-args", "-x 16 -k 1m", # aria2线程等参数设置
    #     "--cookies", path.expanduser("~/.config/bili-sync/cookies.txt"), # cookies读取
    #     "-P", download_path, # 指定存放视频的文件夹路径
    #     "--restrict-filenames", # 自动限制文件名中的字符，使其符合文件系统的要求
    #     "-o", "%(title).50s [%(id)s].%(ext)s" # 限制文件名称长度
    # ]
    # fmt: off
    command = [
        "yutto",
        "-c", configs.credential.sessdata,
        "-d", download_path,
        "--no-danmaku",
        "--no-subtitle",
        "--with-metadata",
        video_url,
    ]
    # fmt: on
    if extra_list_options:
        command.extend(extra_list_options)

    print(f"start downloading {bvid}")
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        print(f"[stdout]\n{stdout.decode()}")
    if stderr:
        print(f"[stderr]\n{stderr.decode()}")
