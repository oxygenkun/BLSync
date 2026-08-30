from pathlib import Path
from unittest.mock import patch

import pytest
from yutto.core.events import DownloadProgress, DownloadStage, DownloadStageChanged
from yutto.core.result import (
    Artifact,
    ArtifactKind,
    DownloadResult,
    ItemResult,
    ItemState,
    ResolvedItem,
    ResolveResult,
)

from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.consumer.yutto_wrapper import (
    YuttoDownloadOptions,
    _BLSyncEventSink,
    _build_yutto_request,
    _result_records,
    iter_download_video_progress,
)
from blsync.progress import DownloadProgressEvent, ProgressEventType, TaskProgressBroker


def _resolved_item(*, cid: int = 100, name: str = "P1") -> ResolvedItem:
    return ResolvedItem.model_validate(
        {
            "avid": "BV1xx411c7mD",
            "cid": str(cid),
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "name": name,
            "title": "Video",
            "cover_url": "https://example.com/cover.jpg",
            "planned_path": name,
        }
    )


def _download_result(path: Path) -> DownloadResult:
    return DownloadResult(
        items=(
            ItemResult(
                state=ItemState.DONE,
                output_path=path,
                artifacts=(Artifact(kind=ArtifactKind.MEDIA, path=path),),
            ),
        )
    )


@pytest.mark.asyncio
async def test_progress_broker_replays_latest_event():
    broker = TaskProgressBroker()
    event = DownloadProgressEvent(
        event=ProgressEventType.PROGRESS,
        task_id=1,
        bvid="BV1",
        status="downloading",
        overall_percent=50.0,
    )
    broker.publish(1, event)

    subscription = broker.subscribe(1)
    assert await anext(subscription) == event
    await subscription.aclose()


def test_build_yutto_request_uses_core_models(tmp_path):
    request = _build_yutto_request(
        YuttoDownloadOptions(
            bvid="BV1",
            download_path=tmp_path,
            is_batch=True,
            selected_episodes=[0, 2],
            name_template="({bvid}){auto}",
        )
    )

    assert request.source.url.endswith("/BV1")
    assert request.scope.batch is True
    assert request.selection.episodes == "1,3"
    assert request.output.directory == tmp_path
    assert request.output.subpath_template == "({bvid}){auto}"
    assert request.resources.metadata is True
    assert request.resources.save_cover is True
    assert request.resources.danmaku is False
    assert request.resources.subtitle is False


def test_event_sink_translates_structured_progress():
    events: list[DownloadProgressEvent] = []
    sink = _BLSyncEventSink(bvid="BV1", emit=events.append, episode_count=2)

    sink.emit(DownloadStageChanged(name=DownloadStage.DOWNLOADING, item="P1"))
    sink.emit(
        DownloadProgress(
            current=25,
            total=100,
            speed_per_second=10.0,
            item="P1",
        )
    )
    sink.emit(DownloadStageChanged(name=DownloadStage.DOWNLOADING, item="P2"))
    sink.emit(
        DownloadProgress(
            current=50,
            total=100,
            speed_per_second=20.0,
            item="P2",
        )
    )

    progress = [event for event in events if event.event is ProgressEventType.PROGRESS]
    assert progress[0].episode_index == 1
    assert progress[0].overall_percent == 12.5
    assert progress[1].episode_index == 2
    assert progress[1].overall_percent == 75.0


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_completed_event(tmp_path):
    video = tmp_path / "video.mp4"

    async def fake_run(_options, _request, emit):
        emit(
            DownloadProgressEvent(
                event=ProgressEventType.PROGRESS,
                task_id=None,
                bvid="BV1",
                status="downloading",
                overall_percent=25.0,
            )
        )
        return ResolveResult(items=(_resolved_item(),)), _download_result(video)

    with patch(
        "blsync.consumer.yutto_wrapper._run_yutto_download",
        side_effect=fake_run,
    ):
        events = [
            event
            async for event in iter_download_video_progress(
                bvid="BV1",
                download_path=tmp_path,
            )
        ]

    assert [event.event for event in events] == [
        ProgressEventType.STATUS,
        ProgressEventType.PROGRESS,
        ProgressEventType.COMPLETED,
    ]
    assert events[-1].overall_percent == 100.0
    assert events[-1].downloaded_files == [str(video)]
    assert events[-1].downloaded_episodes == [
        {
            "path": str(video),
            "page_index": 1,
            "page_cid": 100,
            "page_name": "P1",
        }
    ]


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_failed_event(tmp_path):
    async def fake_run(_options, _request, _emit):
        raise RuntimeError("native transfer failed")

    with patch(
        "blsync.consumer.yutto_wrapper._run_yutto_download",
        side_effect=fake_run,
    ):
        events = [
            event
            async for event in iter_download_video_progress(
                bvid="BV1",
                download_path=tmp_path,
            )
        ]

    assert [event.event for event in events] == [
        ProgressEventType.STATUS,
        ProgressEventType.FAILED,
    ]
    assert events[-1].message == "native transfer failed"


def test_result_records_preserve_selected_page_numbers(tmp_path):
    video = tmp_path / "p3.mp4"
    options = YuttoDownloadOptions(
        bvid="BV1",
        download_path=tmp_path,
        selected_episodes=[2],
    )

    records = _result_records(
        options,
        ResolveResult(items=(_resolved_item(cid=300, name="P3"),)),
        _download_result(video),
    )

    assert records == [
        {
            "path": str(video),
            "page_index": 3,
            "page_cid": 300,
            "page_name": "P3",
        }
    ]


def test_task_context_runtime_task_id_overrides_persisted_placeholder():
    persisted_context = {
        "bid": "BV1",
        "task_name": "fav1",
        "task_id": None,
    }

    context = BiliVideoTaskContext(**{**persisted_context, "task_id": 42})

    assert context.task_id == 42


def test_bili_video_task_expands_yutto_path_prefix_to_video_file(tmp_path):
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")

    files = BiliVideoTask._collect_output_files(tmp_path, [tmp_path / "episode"])

    assert files == [(video.resolve(), "video")]
