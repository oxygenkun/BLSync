"""yutto 2.3 Core API adapter.

This module keeps BLSync's progress-stream contract while using yutto's public,
frontend-independent request, event, and result models directly.
"""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass

from loguru import logger
from yutto.auth import AuthInfo, parse_auth_inline
from yutto.core.application import YuttoApplication
from yutto.core.events import (
    DownloadEvent,
    DownloadProgress,
    DownloadStage,
    DownloadStageChanged,
)
from yutto.core.execution import RequestExecutionScopeFactory
from yutto.core.operation import ReportLevel, bind_download_report_sink
from yutto.core.request import DownloadRequest
from yutto.core.result import ArtifactKind, DownloadResult, ResolveResult
from yutto.download_manager import DownloadManager
from yutto.exceptions import YuttoBaseException

from blsync.progress import DownloadProgressEvent, ProgressEventType


@dataclass(frozen=True)
class YuttoDownloadOptions:
    bvid: str
    download_path: pathlib.Path
    auth: str | None = None
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


class _BLSyncEventSink:
    """Translate yutto 2.3 events to BLSync progress snapshots."""

    def __init__(
        self,
        *,
        bvid: str,
        emit: Callable[[DownloadProgressEvent], None],
        episode_count: int,
    ) -> None:
        self.bvid = bvid
        self.emit_progress = emit
        self.episode_count = max(episode_count, 1)
        self.episode_index = 1
        self.episode_name: str | None = None
        self.completed_episodes = 0
        self._last_item: str | None = None

    def emit(self, event: DownloadEvent) -> None:
        if isinstance(event, DownloadStageChanged):
            self._handle_stage(event)
        elif isinstance(event, DownloadProgress):
            self._handle_progress(event)

    def _handle_stage(self, event: DownloadStageChanged) -> None:
        if event.item is not None and event.item != self._last_item:
            if self._last_item is not None:
                self.completed_episodes = min(
                    self.completed_episodes + 1,
                    self.episode_count - 1,
                )
            self._last_item = event.item
            self.episode_index = min(
                self.completed_episodes + 1,
                self.episode_count,
            )
            self.episode_name = event.item

        status_by_stage = {
            DownloadStage.RESOLVING: "resolving",
            DownloadStage.PREPARING: "preparing",
            DownloadStage.WRITING_RESOURCES: "writing_resources",
            DownloadStage.DOWNLOADING: "downloading",
            DownloadStage.POSTPROCESSING: "postprocessing",
        }
        self.emit_progress(
            DownloadProgressEvent(
                event=ProgressEventType.STATUS,
                task_id=None,
                bvid=self.bvid,
                status=status_by_stage[event.name],
                episode_index=self.episode_index,
                episode_count=self.episode_count,
                episode_name=self.episode_name,
            )
        )

    def _handle_progress(self, event: DownloadProgress) -> None:
        episode_percent = 100.0 if event.total == 0 else event.current / event.total * 100
        overall_percent = (
            (self.completed_episodes + episode_percent / 100)
            / self.episode_count
            * 100
        )
        self.emit_progress(
            DownloadProgressEvent(
                event=ProgressEventType.PROGRESS,
                task_id=None,
                bvid=self.bvid,
                status="downloading",
                overall_percent=min(overall_percent, 100.0),
                episode_index=self.episode_index,
                episode_count=self.episode_count,
                episode_name=event.item or self.episode_name,
                episode_percent=min(episode_percent, 100.0),
                downloaded_bytes=event.current,
                total_bytes=event.total,
                speed_bytes_per_second=max(event.speed_per_second, 0.0),
            )
        )


async def download_video(
    bvid: str,
    download_path: pathlib.Path,
    auth: str | None = None,
    is_batch: bool = False,
    name_template: str | None = None,
    verbose: bool = False,
    selected_episodes: list[int] | None = None,
) -> bool:
    """Download a Bilibili video and return whether yutto completed."""
    async for event in iter_download_video_progress(
        bvid=bvid,
        download_path=download_path,
        auth=auth,
        is_batch=is_batch,
        name_template=name_template,
        verbose=verbose,
        selected_episodes=selected_episodes,
    ):
        if event.event is ProgressEventType.FAILED:
            return False
    return True


async def iter_download_video_progress(
    bvid: str,
    download_path: pathlib.Path,
    auth: str | None = None,
    is_batch: bool = False,
    name_template: str | None = None,
    verbose: bool = False,
    selected_episodes: list[int] | None = None,
) -> AsyncIterator[DownloadProgressEvent]:
    """Yield BLSync progress events from yutto 2.3.1's structured API."""
    options = YuttoDownloadOptions(
        bvid=bvid,
        download_path=download_path,
        auth=auth,
        is_batch=is_batch,
        name_template=name_template,
        verbose=verbose,
        selected_episodes=selected_episodes,
    )
    request = _build_yutto_request(options)
    queue: asyncio.Queue[DownloadProgressEvent | None] = asyncio.Queue()

    def emit(event: DownloadProgressEvent) -> None:
        queue.put_nowait(event)

    async def run() -> None:
        emit(
            DownloadProgressEvent(
                event=ProgressEventType.STATUS,
                task_id=None,
                bvid=bvid,
                status="started",
            )
        )
        try:
            resolved, result = await _run_yutto_download(options, request, emit)
            records = _result_records(options, resolved, result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(f"Failed to download {bvid} with yutto 2.3.1")
            emit(
                DownloadProgressEvent(
                    event=ProgressEventType.FAILED,
                    task_id=None,
                    bvid=bvid,
                    status="failed",
                    message=_error_message(error),
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
                    downloaded_files=[str(record["path"]) for record in records],
                    downloaded_episodes=records,
                )
            )
        finally:
            queue.put_nowait(None)

    worker = asyncio.create_task(run(), name=f"yutto-download-{bvid}")
    try:
        while (event := await queue.get()) is not None:
            yield event
        await worker
    finally:
        if not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


def _build_yutto_request(options: YuttoDownloadOptions) -> DownloadRequest:
    episodes = ",".join(str(number) for number in options.selected_episode_numbers)
    return DownloadRequest.model_validate(
        {
            "source": {"url": options.video_url},
            "scope": {"batch": options.should_use_batch_mode},
            "selection": {"episodes": episodes or "1~-1"},
            "resources": {
                "video": True,
                "audio": True,
                "danmaku": False,
                "subtitle": False,
                "metadata": True,
                "cover": True,
                "chapter_info": True,
                "save_cover": True,
            },
            "output": {
                "directory": options.download_path,
                "subpath_template": options.name_template or "{auto}",
            },
        }
    )


async def _run_yutto_download(
    options: YuttoDownloadOptions,
    request: DownloadRequest,
    emit: Callable[[DownloadProgressEvent], None],
) -> tuple[ResolveResult, DownloadResult]:
    auth = _parse_auth(options.auth)
    scope_factory = RequestExecutionScopeFactory(lambda _request: auth)
    manager = DownloadManager(jobs=1)

    # Resolve first to obtain the public cid/name snapshots used by BLSync's
    # download_files page association. DownloadResult preserves this ordering.
    resolver = YuttoApplication(
        scope_factory,
        workflow=manager,
        resolve_workflow=manager,
    )
    resolved = await resolver.resolve(request)
    sink = _BLSyncEventSink(
        bvid=options.bvid,
        emit=emit,
        episode_count=len(resolved.items),
    )
    application = YuttoApplication(
        scope_factory,
        workflow=manager,
        event_sink=sink,
    )

    def report(
        message: str,
        level: ReportLevel,
        badge: str | None,
        _color: object,
    ) -> None:
        if not options.verbose and level not in {ReportLevel.ERROR, ReportLevel.WARNING}:
            return
        prefix = f"[{badge}] " if badge else ""
        log = logger.debug if level in {ReportLevel.DEBUG, ReportLevel.PLAIN} else logger.info
        if level is ReportLevel.WARNING:
            log = logger.warning
        elif level is ReportLevel.ERROR:
            log = logger.error
        log(f"yutto: {prefix}{message}")

    with bind_download_report_sink(report):
        result = await application.download(request)
    return resolved, result


def _parse_auth(value: str | None) -> AuthInfo | None:
    if value is None:
        logger.warning("No yutto authentication cookie configured")
        return None
    auth = parse_auth_inline(value)
    if auth is None:
        logger.warning("The configured yutto authentication cookie has no SESSDATA")
    return auth


def _result_records(
    options: YuttoDownloadOptions,
    resolved: ResolveResult,
    result: DownloadResult,
) -> list[dict[str, object]]:
    page_numbers = options.selected_episode_numbers
    records: list[dict[str, object]] = []
    for index, item_result in enumerate(result.items):
        resolved_item = resolved.items[index] if index < len(resolved.items) else None
        page_index = page_numbers[index] if index < len(page_numbers) else index + 1
        media_artifacts = [
            artifact
            for artifact in item_result.artifacts
            if artifact.kind is ArtifactKind.MEDIA
        ]
        # A media artifact is also returned for an already-existing output.
        paths = [artifact.path for artifact in media_artifacts] or [item_result.output_path]
        for path in paths:
            records.append(
                {
                    "path": str(path),
                    "page_index": page_index,
                    "page_cid": (
                        int(str(resolved_item.cid)) if resolved_item is not None else None
                    ),
                    "page_name": resolved_item.name if resolved_item is not None else None,
                }
            )
    return records


def _error_message(error: BaseException) -> str:
    if isinstance(error, YuttoBaseException):
        return error.message
    return str(error) or type(error).__name__
