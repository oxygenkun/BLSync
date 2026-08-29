"""
Bilibili消费者模块 - 处理Bilibili相关的下载任务
"""

import os
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
    SavePostprocessConfig,
)
from blsync.consumer.base import Postprocess, Task, TaskContext
from blsync.consumer.yutto_wrapper import iter_download_video_progress
from blsync.database import get_video_dal
from blsync.model.video import VideoModel
from blsync.progress import (
    DownloadProgressEvent,
    ProgressEventType,
    get_progress_broker,
)
from blsync.scraper import BScraper

VIDEO_FILE_SUFFIXES = {".mp4", ".m4v", ".mkv", ".flv", ".mov", ".webm"}

# 输出文件后缀到 download_files.file_type 的映射
FILE_TYPE_BY_SUFFIX = {
    **{suffix: "video" for suffix in VIDEO_FILE_SUFFIXES},
    ".aac": "audio",
    ".mp3": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".jpg": "cover",
    ".jpeg": "cover",
    ".png": "cover",
    ".webp": "cover",
    ".nfo": "metadata",
    ".ass": "subtitle",
    ".srt": "subtitle",
    ".vtt": "subtitle",
    ".xml": "danmaku",
}


def _absolute_project_base() -> pathlib.Path:
    """项目根绝对路径。

    yutto 传回的下载路径（如 ``sync/202608/xxx.mp4``）是相对工作目录的，
    而 ``download_path``（config 中 ``path``）通常也是相对路径。
    因此解析相对路径时应以「项目根」为基准，而不是 ``download_path`` 本身，
    否则会把 ``sync/202608`` 这类前缀重复拼接、导致找不到文件。

    基准优先取 ``BLSYNC_BASE_DIR`` 环境变量，其次为当前工作目录。
    """
    base = os.environ.get("BLSYNC_BASE_DIR")
    if base:
        return pathlib.Path(base).resolve()
    return pathlib.Path.cwd()


def _resolve_yutto_abs_path(yutto_path: pathlib.Path) -> pathlib.Path:
    """把 yutto 报出的路径统一解析为绝对路径。

    - 已是绝对路径：直接 resolve；
    - 相对路径：相对项目根（如 ``sync/202608/xxx.mp4`` -> ``<根>/sync/202608/xxx.mp4``）。
    """
    if yutto_path.is_absolute():
        return yutto_path.resolve()
    return (_absolute_project_base() / yutto_path).resolve()


class BiliVideoTaskContext(TaskContext):
    """Bilibili视频下载任务上下文"""

    bid: str
    task_name: str
    task_id: int | None = None
    selected_episodes: list[int] | None = None  # 选中的分P索引列表


class BiliVideoTask(Task):
    """Bilibili视频下载任务"""

    def __init__(self, task_context: BiliVideoTaskContext):
        self._task_context = task_context
        self._config = get_global_configs()
        self._fav_config = self._config.favorite_list.get(
            self._task_context.task_name, self._config.favorite_list["-1"]
        )
        self.downloaded_files: list[pathlib.Path] = []

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

        video_model = await self._persist_video_info(bs, bid, v_info)

        # 检查是否为多分P视频
        is_batch = v_info.get("videos", 1) > 1
        if is_batch:
            logger.info(f"Video {bid} has {v_info['videos']} parts, using batch mode")

        name_template = (
            self._fav_config.name_group
            if is_batch and self._fav_config.name_group
            else self._fav_config.name
        )

        # cover_path = pathlib.Path(
        #     fav_download_path, repair_filename(f"{v_info['title']}.jpg")
        # )

        download_result = False
        downloaded_paths: list[pathlib.Path] = []
        downloaded_episodes: list[dict] = []
        download_error: str | None = None
        async for event in iter_download_video_progress(
            bvid=bid,
            download_path=fav_download_path,
            auth=_build_yutto_auth(self._config.credential),
            is_batch=is_batch,
            name_template=name_template,
            verbose=self._config.verbose,
            selected_episodes=self._task_context.selected_episodes,
            retry_limit=self._config.download_retry_limit,
            stall_timeout=self._config.download_stall_timeout,
            url_refresh_retries=self._config.download_url_refresh_retries,
        ):
            if event.downloaded_files is not None:
                downloaded_paths = [pathlib.Path(path) for path in event.downloaded_files]
            if event.downloaded_episodes is not None:
                downloaded_episodes = event.downloaded_episodes
            event = self._with_task_id(event)
            if event.event == ProgressEventType.COMPLETED:
                download_result = True
                event = DownloadProgressEvent(
                    **{
                        **event.to_dict(),
                        "event": ProgressEventType.STATUS,
                        "status": "postprocessing",
                        "message": "下载完成，正在进行合并或后处理",
                    }
                )
            elif event.event == ProgressEventType.FAILED:
                download_result = False
                download_error = event.message
            self._publish_progress(event)
            self._log_progress(event)

        # 只有下载成功才记录到数据库并执行后处理
        if download_result:
            output_files = self._collect_output_files(
                fav_download_path, downloaded_paths
            )
            self.downloaded_files = [
                path for path, file_type in output_files if file_type == "video"
            ]
            await self._persist_download_files(
                video_model, fav_download_path, output_files, downloaded_episodes
            )
            logger.info(f"Recorded {bid} to database")

            # 执行下载后处理
            try:
                await self.execute_postprocess()
            except Exception:
                raise Exception(f"Postprocess for {bid} failed")
        else:
            error_detail = download_error or "download ended without a completion event"
            logger.warning(
                f"Skipping postprocess for {bid} due to download failure: "
                f"{error_detail}"
            )
            raise RuntimeError(f"Failed to download video {bid}: {error_detail}")

    @staticmethod
    def _classify_output_file(path: pathlib.Path) -> str | None:
        """按后缀识别输出文件类型，未识别的返回 None。"""
        return FILE_TYPE_BY_SUFFIX.get(path.suffix.lower())

    @classmethod
    def _collect_output_files(
        cls,
        download_path: pathlib.Path,
        yutto_paths: list[pathlib.Path],
    ) -> list[tuple[pathlib.Path, str]]:
        """收集下载产出的最终实体文件（媒体文件及同 stem 的封面、元数据等）。"""
        files: list[tuple[pathlib.Path, str]] = []
        seen: set[pathlib.Path] = set()

        for yutto_path in yutto_paths:
            path = _resolve_yutto_abs_path(yutto_path)
            for candidate in cls._iter_output_candidates(path):
                try:
                    resolved_path = candidate.resolve()
                except OSError as e:
                    logger.warning(f"Failed to resolve downloaded path {candidate}: {e}")
                    continue

                if resolved_path in seen:
                    continue

                file_type = cls._classify_output_file(resolved_path)
                if file_type is None:
                    continue

                seen.add(resolved_path)
                files.append((resolved_path, file_type))

        return files

    @staticmethod
    def _iter_output_candidates(path: pathlib.Path) -> list[pathlib.Path]:
        candidates: list[pathlib.Path] = []

        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(child for child in path.rglob("*") if child.is_file())

        if path.parent.exists():
            # yutto 与媒体文件同 stem 的产物，如 {stem}.nfo、{stem}-poster.jpg
            prefixes = (f"{path.stem}.", f"{path.stem}-")
            candidates.extend(
                child
                for child in path.parent.glob(f"{path.stem}*")
                if child.is_file() and child.name.startswith(prefixes)
            )

        return candidates

    async def _persist_video_info(
        self, bs: BScraper, bid: str, v_info: dict
    ) -> VideoModel | None:
        """把视频元信息与分P信息落库；失败仅记录警告，不影响下载。"""
        try:
            tags = await bs.get_video_tags(bid)
            return await get_video_dal().upsert_video_info(v_info, tags=tags)
        except Exception as e:
            logger.warning(f"Failed to persist video info for {bid}: {e}")
            return None

    async def _persist_download_files(
        self,
        video_model: VideoModel | None,
        download_path: pathlib.Path,
        output_files: list[tuple[pathlib.Path, str]],
        downloaded_episodes: list[dict],
    ) -> None:
        """把下载产出的最终实体文件位置落库；失败仅记录警告。"""
        if video_model is None:
            return
        try:
            video_dal = get_video_dal()
            pages = await video_dal.get_video_pages(video_model.id)
            page_id_by_cid = {
                page.cid: page.id for page in pages if page.cid is not None
            }
            page_id_by_index = {page.page_index: page.id for page in pages}

            # yutto 输出的媒体文件 stem → 分P id，封面/元数据等产物与媒体文件同 stem，一并关联
            page_id_by_stem: dict[tuple[str, str], int] = {}
            for episode in downloaded_episodes:
                raw_path = pathlib.Path(str(episode.get("path", "")))
                if not raw_path.parts:
                    continue
                episode_path = _resolve_yutto_abs_path(raw_path)
                page_id = page_id_by_cid.get(episode.get("page_cid"))
                if page_id is None:
                    page_id = page_id_by_index.get(episode.get("page_index"))
                if page_id is not None:
                    page_id_by_stem[(str(episode_path.parent), episode_path.stem)] = (
                        page_id
                    )

            files = []
            for path, file_type in output_files:
                page_id = page_id_by_stem.get((str(path.parent), path.stem))
                if page_id is None and len(pages) == 1:
                    page_id = pages[0].id
                files.append(
                    {
                        "file_type": file_type,
                        "file_path": str(path),
                        "file_size": path.stat().st_size if path.exists() else None,
                        "page_id": page_id,
                    }
                )

            await video_dal.replace_task_files(
                task_id=self._task_context.task_id,
                video_id=video_model.id,
                files=files,
            )
        except Exception as e:
            logger.warning(
                f"Failed to record download files for {self._task_context.bid}: {e}"
            )

    def _with_task_id(self, event: DownloadProgressEvent) -> DownloadProgressEvent:
        return DownloadProgressEvent(
            **{
                **event.to_dict(),
                "event": event.event,
                "task_id": self._task_context.task_id,
            }
        )

    def _publish_progress(self, event: DownloadProgressEvent) -> None:
        if self._task_context.task_id is None:
            return
        get_progress_broker().publish(self._task_context.task_id, event)

    def _log_progress(self, event: DownloadProgressEvent) -> None:
        if not self._config.verbose or event.event != ProgressEventType.PROGRESS:
            return
        logger.info(
            "download progress "
            f"{event.bvid}: overall={event.overall_percent:.2f}% "
            f"episode={event.episode_index}/{event.episode_count} "
            f"episode_progress={event.episode_percent:.2f}% "
            f"bytes={event.downloaded_bytes}/{event.total_bytes} "
            f"speed={event.speed_bytes_per_second:.0f}B/s"
        )

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
                case SavePostprocessConfig():
                    postprocess_tasks.append(
                        BiliVideoPostprocessSave(self._task_context, post_config)
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


class BiliVideoPostprocessSave(Postprocess):
    """Bilibili视频后处理 - 保存到其他收藏夹并保留原收藏"""

    def __init__(
        self,
        task_context: BiliVideoTaskContext,
        post_config: SavePostprocessConfig,
        config: Config | None = None,
    ):
        self._task_context = task_context
        self._post_config = post_config

        if not config:
            config = get_global_configs()
        self._config = config

    async def execute(self) -> None:
        credential = credential_from_config(self._config.credential)
        to_fid = self._post_config.fid
        video = Video(bvid=self._task_context.bid, credential=credential)

        await video.set_favorite(add_media_ids=[int(to_fid)])
        logger.debug(f"Saved video {self._task_context.bid} to {to_fid}")


@lru_cache(maxsize=1000)
def credential_from_config(config: ConfigCredential) -> Credential:
    return Credential(
        sessdata=config.sessdata,
        bili_jct=config.bili_jct,
        buvid3=config.buvid3,
        dedeuserid=config.dedeuserid,
        ac_time_value=config.ac_time_value,
    )


def _build_yutto_auth(config: ConfigCredential) -> str | None:
    cookies = {
        "SESSDATA": config.sessdata,
        "bili_jct": config.bili_jct,
    }
    cookie_pairs = [f"{name}={value}" for name, value in cookies.items() if value]
    return "; ".join(cookie_pairs) or None


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
