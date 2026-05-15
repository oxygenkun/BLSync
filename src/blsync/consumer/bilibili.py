"""
Bilibili消费者模块 - 处理Bilibili相关的下载任务
"""

import pathlib
from datetime import datetime
from functools import lru_cache

import aiohttp
from bilibili_api import Credential
from bilibili_api.favorite_list import (
    delete_video_favorite_list_content,
    move_video_favorite_list_content,
)
from bilibili_api.video import Video
from loguru import logger

# from yutto.path_templates import repair_filename
from blsync import get_global_configs
from blsync.configs import (
    Config,
    ConfigCredential,
    MovePostprocessConfig,
    RemovePostprocessConfig,
)
from blsync.consumer.base import Postprocess, Task, TaskContext
from blsync.consumer.yutto_wrapper import download_video
from blsync.scraper import BScraper


class BiliVideoTaskContext(TaskContext):
    """Bilibili视频下载任务上下文"""

    bid: str
    task_name: str
    selected_episodes: list[int] | None = None  # 选中的分P索引列表


class BiliVideoTask(Task):
    """Bilibili视频下载任务"""

    def __init__(self, task_context: BiliVideoTaskContext):
        self._task_context = task_context
        self._config = get_global_configs()
        self._fav_config = self._config.favorite_list.get(
            self._task_context.task_name, self._config.favorite_list["-1"]
        )

    def get_task_key(self) -> tuple:
        return (self._task_context.bid, self._task_context.task_name)

    @staticmethod
    def _format_download_path(path_template: str) -> pathlib.Path:
        """格式化下载路径，支持Python format语法"""
        now = datetime.now()
        format_vars = {
            "YYYY": now.strftime("%Y"),  # 四位数年份
            "YY": now.strftime("%y"),  # 两位数年份
            "MM": now.strftime("%m"),  # 两位数月份
            "DD": now.strftime("%d"),  # 两位数日期
            "HH": now.strftime("%H"),  # 两位数小时
            "mm": now.strftime("%M"),  # 两位数分钟
            "SS": now.strftime("%S"),  # 两位数秒数
        }

        try:
            formatted_path = path_template.format(**format_vars)
            return pathlib.Path(formatted_path)
        except KeyError as e:
            logger.warning(
                f"Unknown format variable {e} in path {path_template}, using original path"
            )
            return pathlib.Path(path_template)

    async def execute(self) -> None:
        """Execute video download task"""
        bid = self._task_context.bid

        # 获取下载路径，支持简单和复杂配置格式
        fav_download_path = self._format_download_path(self._fav_config.path)

        if not fav_download_path.parent.exists():
            fav_download_path.mkdir(parents=True, exist_ok=True)

        # 获取视频信息
        bs = BScraper(self._config)
        v_info = await bs.get_video_info(bid)
        if v_info is None:
            logger.info(f"Failed to get video info for {bid}")
            return

        # 检查是否为多分P视频
        is_batch = v_info.get("videos", 1) > 1
        if is_batch:
            logger.info(f"Video {bid} has {v_info['videos']} parts, using batch mode")

        # cover_path = pathlib.Path(
        #     fav_download_path, repair_filename(f"{v_info['title']}.jpg")
        # )

        download_result = await download_video(
            bvid=bid,
            download_path=fav_download_path,
            sessdata=self._config.credential.sessdata,
            is_batch=is_batch,
            name_template=self._fav_config.name,
            verbose=self._config.verbose,
            selected_episodes=self._task_context.selected_episodes,
        )

        # 只有下载成功才记录到数据库并执行后处理
        if download_result:
            logger.info(f"Recorded {bid} to database")

            # 执行下载后处理
            try:
                await self.execute_postprocess()
            except Exception:
                raise Exception(f"Postprocess for {bid} failed")
        else:
            logger.warning(f"Skipping postprocess for {bid} due to download failure")
            raise Exception(f"Failed to download video {bid}")

    async def execute_postprocess(self) -> None:
        if not self._fav_config.postprocess:
            return

        postprocess_tasks = []
        for post_config in self._fav_config.postprocess:
            match post_config:
                case MovePostprocessConfig():
                    postprocess_tasks.append(
                        BiliVideoPostprocessMove(self._task_context, post_config)
                    )
                case RemovePostprocessConfig():
                    postprocess_tasks.append(
                        BiliVideoPostprocessRemove(self._task_context)
                    )
                case _:
                    logger.warning(f"Unknown postprocess action: {post_config.action}")

        for task in postprocess_tasks:
            await task.execute()


class BiliVideoPostprocessMove(Postprocess):
    """Bilibili视频后处理 - 移动到其他收藏夹"""

    def __init__(
        self,
        task_context: BiliVideoTaskContext,
        post_config: MovePostprocessConfig,
        config: Config | None = None,
    ):
        self._task_context = task_context
        self._post_config = post_config

        if not config:
            config = get_global_configs()
        self._config = config

    async def execute(self) -> None:
        bid = self._task_context.bid
        tasks_name = self._task_context.task_name
        credential = credential_from_config(self._config.credential)

        aid = await aid_from_bvid(bid, credential)
        from_fid = self._config.favorite_list[tasks_name].fid
        to_fid = self._post_config.fid

        await move_video_favorite_list_content(
            media_id_from=int(from_fid),
            media_id_to=int(to_fid),
            aids=[aid],
            credential=credential,
        )
        logger.debug(f"Moved video {aid} from {from_fid} to {to_fid}")


class BiliVideoPostprocessRemove(Postprocess):
    """Bilibili视频后处理 - 从收藏夹中移除"""

    def __init__(
        self, task_context: BiliVideoTaskContext, config: Config | None = None
    ):
        self._task_context = task_context

        if not config:
            config = get_global_configs()
        self._config = config

    async def execute(self) -> None:
        credential = credential_from_config(self._config.credential)

        aid = await aid_from_bvid(self._task_context.bid, credential)
        tasks_name = self._task_context.task_name
        fid = self._config.favorite_list[tasks_name].fid
        await delete_video_favorite_list_content(
            media_id=int(fid),
            aids=[aid],
            credential=credential,
        )
        logger.debug(f"Removed video {aid} from {fid}")


@lru_cache(maxsize=1000)
def credential_from_config(config: ConfigCredential) -> Credential:
    return Credential(
        sessdata=config.sessdata,
        bili_jct=config.bili_jct,
        buvid3=config.buvid3,
        dedeuserid=config.dedeuserid,
        ac_time_value=config.ac_time_value,
    )


async def aid_from_bvid(bvid: str, credential: Credential) -> int:
    """从bvid获取aid"""
    v = Video(bvid=bvid, credential=credential)
    video_info = await v.get_info()
    return video_info["aid"]


async def download_file(url, download_path: pathlib.Path):
    """
    下载文件
    """
    if not download_path.parent.exists():
        download_path.mkdir(parents=True, exist_ok=True)

    if download_path.exists():
        # Add suffix if file exists
        stem = download_path.stem
        suffix = download_path.suffix
        counter = 1
        while download_path.exists():
            download_path = download_path.with_name(f"{stem}_{counter}{suffix}")
            counter += 1

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            download_path.write_bytes(await resp.read())
    logger.info(f"Downloaded {url} to {download_path}")
    return True
