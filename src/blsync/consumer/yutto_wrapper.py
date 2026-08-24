"""
yutto 下载封装。

这个模块集中处理 yutto 的参数构造、入口调用、日志过滤、下载路径记录，
以及断点续传状态异常时的清理重试。
"""

import asyncio
import contextvars
import pathlib
import random
import shutil
import ssl
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field

import h2.exceptions
import httpx
import yutto.download_manager as yutto_download_manager
import yutto.downloader.downloader as yutto_downloader
import yutto.downloader.progressbar as yutto_progressbar
from loguru import logger
from yutto.__main__ import flatten_args, run_download
from yutto.cli.cli import cli, handle_default_subcommand
from yutto.utils.console.logger import Logger as YuttoLogger
from yutto.utils.fetcher import Fetcher, FetcherContext
from yutto.utils.file_buffer import AsyncFileBuffer
from yutto.validator import initial_validation

from blsync.progress import DownloadProgressEvent, ProgressEventType

_yutto_download_paths: contextvars.ContextVar[list[pathlib.Path] | None] = (
    contextvars.ContextVar("_yutto_download_paths", default=None)
)
_yutto_output_paths: contextvars.ContextVar[list[pathlib.Path] | None] = (
    contextvars.ContextVar("_yutto_output_paths", default=None)
)
_suppress_yutto_info: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_suppress_yutto_info", default=False
)
_yutto_progress_callback: contextvars.ContextVar[
    Callable[[DownloadProgressEvent], None] | None
] = contextvars.ContextVar("_yutto_progress_callback", default=None)
_yutto_bvid: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_yutto_bvid", default=None
)
_yutto_episode_index: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_yutto_episode_index", default=None
)
_yutto_episode_count: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_yutto_episode_count", default=None
)
_yutto_episode_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_yutto_episode_name", default=None
)
_yutto_completed_episode_progress: contextvars.ContextVar[float] = (
    contextvars.ContextVar("_yutto_completed_episode_progress", default=0.0)
)
_yutto_cancel_event: contextvars.ContextVar[threading.Event | None] = (
    contextvars.ContextVar("_yutto_cancel_event", default=None)
)
_yutto_retry_limit: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_yutto_retry_limit", default=10
)
_yutto_stall_timeout: contextvars.ContextVar[float] = contextvars.ContextVar(
    "_yutto_stall_timeout", default=120.0
)

_original_yutto_process_download = yutto_download_manager.process_download
_original_yutto_logger_info = YuttoLogger.info
_original_yutto_logger_custom = YuttoLogger.custom
_original_yutto_logger_new_line = YuttoLogger.new_line
_original_yutto_show_progress = yutto_progressbar.show_progress
_original_yutto_downloader_show_progress = yutto_downloader.show_progress


@dataclass(frozen=True)
class YuttoDownloadOptions:
    bvid: str
    download_path: pathlib.Path
    auth: str | None = None
    extra_list_options: list[str] = field(default_factory=list)
    is_batch: bool = False
    name_template: str | None = None
    verbose: bool = False
    selected_episodes: list[int] | None = None
    retry_limit: int = 10
    stall_timeout: float = 120.0
    url_refresh_retries: int = 2

    @property
    def video_url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"

    @property
    def should_use_batch_mode(self) -> bool:
        return bool(self.selected_episodes) or self.is_batch

    @property
    def selected_episode_numbers(self) -> list[int]:
        if not self.selected_episodes:
            return []
        return [episode + 1 for episode in sorted(self.selected_episodes)]


class YuttoRecoverableDownloadError(Exception):
    """A yutto failure that can be retried after removing partial files."""

    def __init__(self, paths: list[pathlib.Path]):
        self.paths = paths
        super().__init__("yutto partial download state is invalid")


class YuttoDownloadStalledError(Exception):
    """A media block stopped making progress after bounded reconnect attempts."""


class YuttoDownloadCancelledError(Exception):
    """The owning BLSync task requested cooperative downloader shutdown."""


async def download_video(
    bvid: str,
    download_path: pathlib.Path,
    auth: str | None = None,
    extra_list_options: list[str] | None = None,
    is_batch: bool = False,
    name_template: str | None = None,
    verbose: bool = False,
    selected_episodes: list[int] | None = None,
) -> bool:
    """
    使用 yutto 下载视频。

    :param bvid: 视频的bvid
    :param download_path: 存放视频的文件夹路径
    :param auth: Bilibili cookie string, for example ``SESSDATA=...; bili_jct=...``
    :param extra_list_options: 其他自定义参数
    :param is_batch: 是否为多分P视频，若为True则添加--batch参数
    :param name_template: 文件名模板
    :param verbose: 详细输出
    :param selected_episodes: 选中的分P索引列表（0-based）
    """
    options = YuttoDownloadOptions(
        bvid=bvid,
        download_path=download_path,
        auth=auth,
        extra_list_options=extra_list_options or [],
        is_batch=is_batch,
        name_template=name_template,
        verbose=verbose,
        selected_episodes=selected_episodes,
    )

    yutto_args = _build_yutto_args(options)
    logger.info(f"start downloading {options.bvid}")
    logger.debug(f"run yutto with args: {' '.join(yutto_args)}")

    try:
        await _run_yutto_download_in_thread(yutto_args, options.verbose)
    except YuttoRecoverableDownloadError as e:
        logger.warning(
            f"yutto resume state is invalid for {options.bvid}; "
            "cleaning partial downloads and retrying once"
        )
        _cleanup_yutto_partial_downloads(
            options.download_path,
            e.paths,
            options.should_use_batch_mode,
        )
        return await _retry_yutto_download(options, yutto_args)
    except SystemExit as e:
        logger.exception(
            f"Failed to download {options.bvid}, yutto exited with code: {e.code}"
        )
        return False
    except Exception:
        logger.exception(
            f"Failed to download {options.bvid}, yutto raised an exception"
        )
        return False

    logger.info(f"end downloaded {options.bvid}")
    return True


async def iter_download_video_progress(
    bvid: str,
    download_path: pathlib.Path,
    auth: str | None = None,
    extra_list_options: list[str] | None = None,
    is_batch: bool = False,
    name_template: str | None = None,
    verbose: bool = False,
    selected_episodes: list[int] | None = None,
    retry_limit: int = 10,
    stall_timeout: float = 120.0,
    url_refresh_retries: int = 2,
) -> AsyncIterator[DownloadProgressEvent]:
    """Yield structured yutto download progress events."""
    options = YuttoDownloadOptions(
        bvid=bvid,
        download_path=download_path,
        auth=auth,
        extra_list_options=extra_list_options or [],
        is_batch=is_batch,
        name_template=name_template,
        verbose=verbose,
        selected_episodes=selected_episodes,
        retry_limit=retry_limit,
        stall_timeout=stall_timeout,
        url_refresh_retries=url_refresh_retries,
    )
    yutto_args = _build_yutto_args(options)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[DownloadProgressEvent | None] = asyncio.Queue()

    def emit(event: DownloadProgressEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def run_download() -> None:
        retry_limit_token = _yutto_retry_limit.set(max(options.retry_limit, 1))
        stall_timeout_token = _yutto_stall_timeout.set(
            max(options.stall_timeout, 1.0)
        )
        emit(
            DownloadProgressEvent(
                event=ProgressEventType.STATUS,
                task_id=None,
                bvid=bvid,
                status="started",
            )
        )
        cleaned_invalid_resume = False
        url_refresh_attempts = 0
        try:
            while True:
                try:
                    downloaded_paths = await _run_yutto_download_in_thread(
                        yutto_args,
                        options.verbose,
                        emit,
                        bvid,
                    )
                    break
                except YuttoRecoverableDownloadError as error:
                    if cleaned_invalid_resume:
                        raise
                    cleaned_invalid_resume = True
                    emit(
                        DownloadProgressEvent(
                            event=ProgressEventType.STATUS,
                            task_id=None,
                            bvid=bvid,
                            status="retrying",
                            message="invalid resume state; cleaning partial files",
                        )
                    )
                    _cleanup_yutto_partial_downloads(
                        options.download_path,
                        error.paths,
                        options.should_use_batch_mode,
                    )
                except YuttoDownloadStalledError as error:
                    if url_refresh_attempts >= options.url_refresh_retries:
                        raise
                    url_refresh_attempts += 1
                    delay = min(2 ** (url_refresh_attempts - 1), 10)
                    emit(
                        DownloadProgressEvent(
                            event=ProgressEventType.STATUS,
                            task_id=None,
                            bvid=bvid,
                            status="refreshing",
                            message=(
                                f"download stalled; refreshing media URL "
                                f"({url_refresh_attempts}/"
                                f"{options.url_refresh_retries})"
                            ),
                        )
                    )
                    logger.warning(
                        f"Download {bvid} stalled: {error}; "
                        f"refreshing media URL after {delay}s"
                    )
                    await asyncio.sleep(delay)
        except SystemExit as e:
            message = f"yutto exited with code {e.code}"
            logger.exception(f"Failed to download {bvid}: {message}")
            emit(
                DownloadProgressEvent(
                    event=ProgressEventType.FAILED,
                    task_id=None,
                    bvid=bvid,
                    status="failed",
                    message=message,
                )
            )
        except Exception as e:
            logger.exception(f"Failed to download {bvid}")
            emit(
                DownloadProgressEvent(
                    event=ProgressEventType.FAILED,
                    task_id=None,
                    bvid=bvid,
                    status="failed",
                    message=str(e),
                )
            )
        else:
            emit(
                DownloadProgressEvent(
                    event=ProgressEventType.COMPLETED,
                    task_id=None,
                    bvid=bvid,
                    status="completed",
                    overall_percent=100.0,
                    downloaded_files=[str(path) for path in downloaded_paths],
                )
            )
        finally:
            _yutto_stall_timeout.reset(stall_timeout_token)
            _yutto_retry_limit.reset(retry_limit_token)
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(run_download())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
        await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _install_yutto_patches() -> None:
    yutto_download_manager.process_download = _record_yutto_process_download
    Fetcher.download_file_with_offset = staticmethod(
        _bounded_yutto_download_file_with_offset
    )
    YuttoLogger.info = classmethod(_filtered_yutto_logger_info)
    YuttoLogger.custom = classmethod(_filtered_yutto_logger_custom)
    YuttoLogger.new_line = classmethod(_filtered_yutto_logger_new_line)
    yutto_progressbar.show_progress = _capture_yutto_show_progress
    yutto_downloader.show_progress = _capture_yutto_show_progress


async def _record_yutto_process_download(ctx, client, episode_data, options):
    paths = _yutto_download_paths.get()
    if paths is not None:
        paths.append(pathlib.Path(episode_data["path"]))
    output_path: pathlib.Path | None = None
    output_paths = _yutto_output_paths.get()
    if output_paths is not None:
        try:
            output_path = _resolve_yutto_output_path(episode_data, options)
            output_paths.append(output_path)
        except Exception as e:
            logger.warning(
                f"Failed to resolve yutto output path for {episode_data['path']}: {e}"
            )
            output_paths.append(pathlib.Path(episode_data["path"]))
    _yutto_episode_name.set(pathlib.Path(episode_data["path"]).name)
    if _yutto_episode_index.get() is None:
        _yutto_episode_index.set(1)
    if _yutto_episode_count.get() is None:
        _yutto_episode_count.set(1)
    if (
        output_path is not None
        and output_path.exists()
        and not options.get("overwrite", False)
    ):
        logger.info(f"Yutto output file already exists, skip download: {output_path}")
        _yutto_completed_episode_progress.set(float(_yutto_episode_index.get() or 1))
        return yutto_downloader.DownloadState.SKIP
    result = await _original_yutto_process_download(ctx, client, episode_data, options)
    _yutto_completed_episode_progress.set(float(_yutto_episode_index.get() or 1))
    return result


def _resolve_yutto_output_path(episode_data, options) -> pathlib.Path:
    output_dir, _tmp_dir, filename = yutto_downloader.resolve_path(
        options["output_dir"],
        options["tmp_dir"],
        episode_data["path"],
    )

    videos = episode_data["videos"]
    audios = episode_data["audios"]
    video = yutto_downloader.select_video(
        videos,
        options["video_quality"],
        options["video_download_codec"],
        options["video_download_codec_priority"],
    )
    audio = yutto_downloader.select_audio(
        audios,
        options["audio_quality"],
        options["audio_download_codec"],
    )
    will_download_video = video is not None and options["require_video"]
    will_download_audio = audio is not None and options["require_audio"]

    output_format = ".mp4"
    if not will_download_video:
        if options["output_format_audio_only"] != "infer":
            output_format = "." + options["output_format_audio_only"]
        elif will_download_audio and audio["codec"] == "flac":
            output_format = ".flac"
        else:
            output_format = ".m4a"
    elif options["output_format"] != "infer":
        output_format = "." + options["output_format"]
    elif will_download_audio and audio is not None and audio["codec"] == "flac":
        output_format = ".mkv"

    return output_dir / f"{filename}{output_format}"


def _filtered_yutto_logger_info(cls, string, *print_args, **print_kwargs):
    if _suppress_yutto_info.get():
        return
    _original_yutto_logger_info(string, *print_args, **print_kwargs)


def _filtered_yutto_logger_custom(cls, string, badge, *print_args, **print_kwargs):
    badge_text = str(getattr(badge, "text", ""))
    if badge_text.startswith("[") and badge_text.endswith("]") and "/" in badge_text:
        current, total = badge_text[1:-1].split("/", maxsplit=1)
        if current.isdigit() and total.isdigit():
            _yutto_episode_index.set(int(current))
            _yutto_episode_count.set(int(total))
            _yutto_episode_name.set(str(string))
    if _suppress_yutto_info.get() and str(getattr(badge, "text", "")) == "大会员":
        return
    _original_yutto_logger_custom(string, badge, *print_args, **print_kwargs)


def _filtered_yutto_logger_new_line(cls):
    if _suppress_yutto_info.get():
        return
    _original_yutto_logger_new_line()


def _build_yutto_args(options: YuttoDownloadOptions) -> list[str]:
    args = [
        "-d",
        str(options.download_path),
        "--no-danmaku",
        "--no-subtitle",
        "--with-metadata",
        "--save-cover",
        "--no-color",
        "--no-progress",
    ]

    _append_cookie_args(args, options)
    _append_episode_args(args, options)
    _append_output_template_args(args, options)
    args.extend(options.extra_list_options)
    args.append(options.video_url)
    return args


def _append_cookie_args(args: list[str], options: YuttoDownloadOptions) -> None:
    if options.auth:
        args.extend(["--auth", options.auth])
    else:
        logger.warning("no auth")


def _append_episode_args(args: list[str], options: YuttoDownloadOptions) -> None:
    if options.selected_episodes:
        args.append("--batch")
        episodes_str = ",".join(str(i) for i in options.selected_episode_numbers)
        args.extend(["-p", episodes_str])
        logger.info(
            f"Added -p {episodes_str} for episodes "
            f"(0-based: {options.selected_episodes})"
        )
    elif options.is_batch:
        args.append("--batch")
        logger.info(f"Added --batch parameter for multi-part video {options.bvid}")


def _append_output_template_args(
    args: list[str], options: YuttoDownloadOptions
) -> None:
    if not options.name_template:
        return

    args.extend(["--subpath-template", options.name_template])
    logger.info(
        f"Added --subpath-template parameter with template: {options.name_template}"
    )


async def _run_yutto_download_in_thread(
    yutto_args: list[str],
    verbose: bool,
    progress_callback: Callable[[DownloadProgressEvent], None] | None = None,
    bvid: str | None = None,
) -> list[pathlib.Path]:
    cancel_event = threading.Event()
    worker = asyncio.create_task(
        asyncio.to_thread(
            _run_yutto_download,
            yutto_args,
            verbose,
            progress_callback,
            bvid,
            cancel_event,
            _yutto_retry_limit.get(),
            _yutto_stall_timeout.get(),
        )
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=10)
        except TimeoutError:
            logger.warning("Timed out while stopping the yutto worker thread")
        except Exception as error:
            logger.debug(f"Yutto worker stopped during cancellation: {error}")
        raise


async def _retry_yutto_download(
    options: YuttoDownloadOptions,
    yutto_args: list[str],
) -> bool:
    try:
        await _run_yutto_download_in_thread(yutto_args, options.verbose)
    except SystemExit as retry_error:
        logger.exception(
            f"Failed to download {options.bvid} after cleanup, "
            f"yutto exited with code: {retry_error.code}"
        )
        return False
    except Exception:
        logger.exception(
            f"Failed to download {options.bvid} after cleanup, yutto raised an exception"
        )
        return False

    logger.info(f"end downloaded {options.bvid}")
    return True


def _run_yutto_download(
    yutto_args: list[str],
    verbose: bool,
    progress_callback: Callable[[DownloadProgressEvent], None] | None = None,
    bvid: str | None = None,
    cancel_event: threading.Event | None = None,
    retry_limit: int = 10,
    stall_timeout: float = 120.0,
) -> list[pathlib.Path]:
    """
    Run yutto directly through its Python entry points.

    yutto's initialization path calls asyncio.run(), so this helper is executed
    in a worker thread by download_video().
    """
    parser = cli()
    args = parser.parse_args(handle_default_subcommand(yutto_args))
    ctx = FetcherContext()
    initial_validation(ctx, args)
    paths: list[pathlib.Path] = []
    output_paths: list[pathlib.Path] = []
    paths_token = _yutto_download_paths.set(paths)
    output_paths_token = _yutto_output_paths.set(output_paths)
    suppress_token = _suppress_yutto_info.set(not verbose)
    callback_token = _yutto_progress_callback.set(progress_callback)
    bvid_token = _yutto_bvid.set(bvid)
    episode_index_token = _yutto_episode_index.set(None)
    episode_count_token = _yutto_episode_count.set(None)
    episode_name_token = _yutto_episode_name.set(None)
    completed_token = _yutto_completed_episode_progress.set(0.0)
    cancel_token = _yutto_cancel_event.set(cancel_event)
    retry_limit_token = _yutto_retry_limit.set(max(retry_limit, 1))
    stall_timeout_token = _yutto_stall_timeout.set(max(stall_timeout, 1.0))
    try:
        run_download(ctx, flatten_args(args, parser))
        return output_paths
    except Exception as e:
        if _is_yutto_invalid_resume_error(e):
            raise YuttoRecoverableDownloadError(paths) from e
        raise
    finally:
        _yutto_stall_timeout.reset(stall_timeout_token)
        _yutto_retry_limit.reset(retry_limit_token)
        _yutto_cancel_event.reset(cancel_token)
        _yutto_completed_episode_progress.reset(completed_token)
        _yutto_episode_name.reset(episode_name_token)
        _yutto_episode_count.reset(episode_count_token)
        _yutto_episode_index.reset(episode_index_token)
        _yutto_bvid.reset(bvid_token)
        _yutto_progress_callback.reset(callback_token)
        _suppress_yutto_info.reset(suppress_token)
        _yutto_output_paths.reset(output_paths_token)
        _yutto_download_paths.reset(paths_token)


async def _bounded_yutto_download_file_with_offset(
    ctx: FetcherContext,
    client: httpx.AsyncClient,
    url: str,
    mirrors: list[str],
    file_buffer: AsyncFileBuffer,
    offset: int,
    size: int | None,
) -> None:
    """Download one block with bounded retries, stall detection and cancellation."""
    async with ctx.download_guard():
        headers = client.headers.copy()
        url_pool = [url, *mirrors]
        block_offset = 0
        consecutive_failures = 0
        last_progress_at = time.monotonic()

        while size is None or block_offset < size:
            _raise_if_yutto_cancelled()
            stalled_for = time.monotonic() - last_progress_at
            if stalled_for >= _yutto_stall_timeout.get():
                raise YuttoDownloadStalledError(
                    f"{file_buffer.file_path} made no progress for "
                    f"{stalled_for:.1f}s"
                )

            selected_url = random.choice(url_pool)
            range_end = offset + size - 1 if size is not None else ""
            headers["Range"] = f"bytes={offset + block_offset}-{range_end}"

            try:
                async with client.stream(
                    "GET",
                    selected_url,
                    headers=headers,
                    timeout=httpx.Timeout(7, connect=3),
                ) as response:
                    response.raise_for_status()
                    received_bytes = 0
                    async for chunk in response.aiter_bytes(2**16):
                        _raise_if_yutto_cancelled()
                        await file_buffer.write(chunk, offset + block_offset)
                        chunk_size = len(chunk)
                        block_offset += chunk_size
                        received_bytes += chunk_size
                        consecutive_failures = 0
                        last_progress_at = time.monotonic()

                    if size is None or block_offset >= size:
                        return
                    if received_bytes == 0:
                        raise httpx.RemoteProtocolError(
                            "media response ended before the requested range"
                        )
            except (
                httpx.HTTPError,
                h2.exceptions.H2Error,
                ssl.SSLError,
                ValueError,
            ) as error:
                if isinstance(error, ValueError) and (
                    "semaphore released too many times" not in str(error).lower()
                ):
                    raise
                consecutive_failures += 1
                error_type = type(error).__name__
                logger.warning(
                    f"File {file_buffer.file_path} download failed "
                    f"({error_type}); retry "
                    f"{consecutive_failures}/{_yutto_retry_limit.get()}"
                )
                if consecutive_failures >= _yutto_retry_limit.get():
                    raise YuttoDownloadStalledError(
                        f"{file_buffer.file_path} exceeded "
                        f"{_yutto_retry_limit.get()} consecutive retries "
                        f"after {error_type}"
                    ) from error

                stalled_for = time.monotonic() - last_progress_at
                if stalled_for >= _yutto_stall_timeout.get():
                    raise YuttoDownloadStalledError(
                        f"{file_buffer.file_path} made no progress for "
                        f"{stalled_for:.1f}s after {error_type}"
                    ) from error

                retry_delay = min(0.5 * 2 ** (consecutive_failures - 1), 5.0)
                await asyncio.sleep(retry_delay)


def _raise_if_yutto_cancelled() -> None:
    cancel_event = _yutto_cancel_event.get()
    if cancel_event is not None and cancel_event.is_set():
        raise YuttoDownloadCancelledError("yutto download was cancelled")


async def _capture_yutto_show_progress(file_buffers, total_size: int) -> None:
    """Mirror yutto's progress loop while emitting structured updates."""
    start_time = time.time()
    previous_size = sum(file_buffer.written_size for file_buffer in file_buffers)
    while True:
        buffered_size = sum(
            sum(len(chunk.data) for chunk in file_buffer.buffer)
            for file_buffer in file_buffers
        )
        written_size = sum(file_buffer.written_size for file_buffer in file_buffers)
        current_time = time.time()
        current_size = written_size + buffered_size
        speed = (current_size - previous_size) / (current_time - start_time + 10**-6)
        episode_percent = 100.0 if total_size == 0 else current_size / total_size * 100
        episode_index = _yutto_episode_index.get() or 1
        episode_count = _yutto_episode_count.get() or 1
        overall_percent = (
            (_yutto_completed_episode_progress.get() + episode_percent / 100)
            / episode_count
        ) * 100
        callback = _yutto_progress_callback.get()
        bvid = _yutto_bvid.get()
        if callback is not None and bvid is not None:
            callback(
                DownloadProgressEvent(
                    event=ProgressEventType.PROGRESS,
                    task_id=None,
                    bvid=bvid,
                    status="downloading",
                    overall_percent=min(overall_percent, 100.0),
                    episode_index=episode_index,
                    episode_count=episode_count,
                    episode_name=_yutto_episode_name.get(),
                    episode_percent=min(episode_percent, 100.0),
                    downloaded_bytes=current_size,
                    total_bytes=total_size,
                    speed_bytes_per_second=max(speed, 0.0),
                )
            )
        start_time, previous_size = current_time, current_size
        await asyncio.sleep(0.25)
        if total_size == current_size:
            break


def _is_yutto_invalid_resume_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, AssertionError)
            and "起始地址" in str(current)
            and "大于总地址" in str(current)
        ):
            return True
        current = current.__cause__ or current.__context__

    return False


def _cleanup_yutto_partial_downloads(
    download_path: pathlib.Path,
    yutto_paths: list[pathlib.Path],
    is_batch: bool,
) -> None:
    base_path = download_path.resolve()
    targets: set[pathlib.Path] = set()

    for yutto_path in yutto_paths:
        if yutto_path.is_absolute() or not yutto_path.parts:
            logger.warning(f"Skip unsafe yutto cleanup path: {yutto_path}")
            continue

        if is_batch and len(yutto_path.parts) > 1:
            targets.add(base_path / yutto_path.parts[0])
            continue

        target_parent = base_path / yutto_path.parent
        if target_parent.exists():
            targets.update(target_parent.glob(f"{yutto_path.name}*"))
        targets.add(base_path / yutto_path)

    for target in sorted(targets, key=lambda path: len(path.parts), reverse=True):
        try:
            resolved_target = target.resolve()
        except OSError as e:
            logger.warning(f"Failed to resolve cleanup target {target}: {e}")
            continue

        if resolved_target == base_path or base_path not in resolved_target.parents:
            logger.warning(f"Skip unsafe yutto cleanup target: {resolved_target}")
            continue

        if resolved_target.is_dir():
            logger.warning(
                f"Removing yutto partial download directory: {resolved_target}"
            )
            shutil.rmtree(resolved_target)
        elif resolved_target.exists():
            logger.warning(f"Removing yutto partial download file: {resolved_target}")
            resolved_target.unlink()


_install_yutto_patches()
