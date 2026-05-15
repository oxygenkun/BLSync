from unittest.mock import patch

import pytest

from blsync.consumer.bilibili import BiliVideoTaskContext
from blsync.consumer.yutto_wrapper import (
    YuttoDownloadOptions,
    _build_yutto_args,
    _capture_yutto_show_progress,
    iter_download_video_progress,
)
from blsync.progress import DownloadProgressEvent, ProgressEventType, TaskProgressBroker


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


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_completed_event(tmp_path):
    async def fake_run(_args, _verbose, callback, bvid):
        callback(
            DownloadProgressEvent(
                event=ProgressEventType.PROGRESS,
                task_id=None,
                bvid=bvid,
                status="downloading",
                overall_percent=25.0,
                episode_index=1,
                episode_count=1,
                episode_percent=25.0,
                downloaded_bytes=25,
                total_bytes=100,
                speed_bytes_per_second=10.0,
            )
        )

    with patch(
        "blsync.consumer.yutto_wrapper._run_yutto_download_in_thread",
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


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_retrying_event(tmp_path):
    from blsync.consumer.yutto_wrapper import YuttoRecoverableDownloadError

    calls = 0

    async def fake_run(_args, _verbose, _callback, _bvid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise YuttoRecoverableDownloadError([])

    with patch(
        "blsync.consumer.yutto_wrapper._run_yutto_download_in_thread",
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
        ProgressEventType.STATUS,
        ProgressEventType.COMPLETED,
    ]
    assert events[1].status == "retrying"


def test_task_context_runtime_task_id_overrides_persisted_placeholder():
    persisted_context = {
        "bid": "BV1",
        "task_name": "fav1",
        "task_id": None,
    }

    context = BiliVideoTaskContext(**{**persisted_context, "task_id": 42})

    assert context.task_id == 42


def test_yutto_downloader_uses_captured_progress_function():
    import yutto.downloader.downloader as yutto_downloader
    import yutto.downloader.progressbar as yutto_progressbar

    assert yutto_progressbar.show_progress is _capture_yutto_show_progress
    assert yutto_downloader.show_progress is _capture_yutto_show_progress


def test_yutto_uses_auth_cookie_argument(tmp_path):
    args = _build_yutto_args(
        YuttoDownloadOptions(
            bvid="BV1",
            download_path=tmp_path,
            auth="SESSDATA=sess; bili_jct=jct",
        )
    )

    assert "--auth" in args
    assert "SESSDATA=sess; bili_jct=jct" in args
    assert "--sessdata" not in args
