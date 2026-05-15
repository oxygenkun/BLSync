"""
yutto 下载封装。

这个模块集中处理 yutto 的参数构造、入口调用、日志过滤、下载路径记录，
以及断点续传状态异常时的清理重试。
"""

import asyncio
import contextvars
import pathlib
import shutil
from dataclasses import dataclass, field

import yutto.download_manager as yutto_download_manager
from loguru import logger
from yutto.__main__ import flatten_args, run_download
from yutto.cli.cli import cli, handle_default_subcommand
from yutto.utils.console.logger import Logger as YuttoLogger
from yutto.utils.fetcher import FetcherContext
from yutto.validator import initial_validation

_yutto_download_paths: contextvars.ContextVar[list[pathlib.Path] | None] = (
    contextvars.ContextVar("_yutto_download_paths", default=None)
)
_suppress_yutto_info: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_suppress_yutto_info", default=False
)

_original_yutto_process_download = yutto_download_manager.process_download
_original_yutto_logger_info = YuttoLogger.info
_original_yutto_logger_custom = YuttoLogger.custom
_original_yutto_logger_new_line = YuttoLogger.new_line


@dataclass(frozen=True)
class YuttoDownloadOptions:
    bvid: str
    download_path: pathlib.Path
    sessdata: str | None = None
    extra_list_options: list[str] = field(default_factory=list)
    is_batch: bool = False
    name_template: str | None = None
    verbose: bool = False
    selected_episodes: list[int] | None = None

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


async def download_video(
    bvid: str,
    download_path: pathlib.Path,
    sessdata: str | None = None,
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
    :param sessdata: sessdata cookie
    :param extra_list_options: 其他自定义参数
    :param is_batch: 是否为多分P视频，若为True则添加--batch参数
    :param name_template: 文件名模板
    :param verbose: 详细输出
    :param selected_episodes: 选中的分P索引列表（0-based）
    """
    options = YuttoDownloadOptions(
        bvid=bvid,
        download_path=download_path,
        sessdata=sessdata,
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
        logger.exception(f"Failed to download {options.bvid}, yutto raised an exception")
        return False

    logger.info(f"end downloaded {options.bvid}")
    return True


def _install_yutto_patches() -> None:
    yutto_download_manager.process_download = _record_yutto_process_download
    YuttoLogger.info = classmethod(_filtered_yutto_logger_info)
    YuttoLogger.custom = classmethod(_filtered_yutto_logger_custom)
    YuttoLogger.new_line = classmethod(_filtered_yutto_logger_new_line)


async def _record_yutto_process_download(ctx, client, episode_data, options):
    paths = _yutto_download_paths.get()
    if paths is not None:
        paths.append(pathlib.Path(episode_data["path"]))
    return await _original_yutto_process_download(ctx, client, episode_data, options)


def _filtered_yutto_logger_info(cls, string, *print_args, **print_kwargs):
    if _suppress_yutto_info.get():
        return
    _original_yutto_logger_info(string, *print_args, **print_kwargs)


def _filtered_yutto_logger_custom(cls, string, badge, *print_args, **print_kwargs):
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
    if options.sessdata:
        args.extend(["-c", options.sessdata])
    else:
        logger.warning("no sessdata")


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


def _append_output_template_args(args: list[str], options: YuttoDownloadOptions) -> None:
    if not options.name_template:
        return

    args.extend(["--subpath-template", options.name_template])
    logger.info(
        f"Added --subpath-template parameter with template: {options.name_template}"
    )


async def _run_yutto_download_in_thread(yutto_args: list[str], verbose: bool) -> None:
    await asyncio.to_thread(_run_yutto_download, yutto_args, verbose)


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


def _run_yutto_download(yutto_args: list[str], verbose: bool) -> None:
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
    paths_token = _yutto_download_paths.set(paths)
    suppress_token = _suppress_yutto_info.set(not verbose)
    try:
        run_download(ctx, flatten_args(args, parser))
    except Exception as e:
        if _is_yutto_invalid_resume_error(e):
            raise YuttoRecoverableDownloadError(paths) from e
        raise
    finally:
        _suppress_yutto_info.reset(suppress_token)
        _yutto_download_paths.reset(paths_token)


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
