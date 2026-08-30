from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from blsync.consumer.bilibili import BiliVideoTask, BiliVideoTaskContext
from blsync.consumer.yutto_wrapper import (
    YuttoDownloadOptions,
    YuttoMergeFailedError,
    _build_yutto_args,
    _capture_yutto_show_progress,
    _RaisingFFmpegProxy,
    _record_yutto_process_download,
    _resolve_yutto_output_path,
    _yutto_output_records,
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
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
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
        return [
            {
                "path": video,
                "page_index": 1,
                "page_cid": 100,
                "page_name": "P1",
            }
        ]

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
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
        return [
            {
                "path": video,
                "page_index": 1,
                "page_cid": 100,
                "page_name": "P1",
            }
        ]

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
    assert events[-1].downloaded_files == [str(tmp_path / "video.mp4")]


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_downloaded_files(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    async def fake_run(_args, _verbose, _callback, _bvid):
        return [
            {
                "path": video,
                "page_index": 1,
                "page_cid": 100,
                "page_name": "P1",
            }
        ]

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

    assert events[-1].event == ProgressEventType.COMPLETED
    assert events[-1].downloaded_files == [str(video)]
    assert events[-1].downloaded_episodes == [
        {
            "path": str(video),
            "page_index": 1,
            "page_cid": 100,
            "page_name": "P1",
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


def test_yutto_output_path_uses_yutto_resolved_filename(tmp_path):
    episode_data = {
        "path": "episode",
        "videos": ["video"],
        "audios": ["audio"],
    }
    options = {
        "output_dir": tmp_path,
        "tmp_dir": tmp_path,
        "video_quality": 0,
        "video_download_codec": "avc",
        "video_download_codec_priority": None,
        "audio_quality": 0,
        "audio_download_codec": "mp4a",
        "require_video": True,
        "require_audio": True,
        "output_format": "infer",
        "output_format_audio_only": "infer",
    }

    with (
        patch(
            "blsync.consumer.yutto_wrapper.yutto_downloader.select_video",
            return_value={"codec": "avc"},
        ),
        patch(
            "blsync.consumer.yutto_wrapper.yutto_downloader.select_audio",
            return_value={"codec": "mp4a"},
        ),
    ):
        path = _resolve_yutto_output_path(episode_data, options)

    assert path == tmp_path / "episode.mp4"


@pytest.mark.asyncio
async def test_yutto_output_path_resolution_failure_does_not_fail_download(tmp_path):
    async def fake_process_download(_ctx, _client, _episode_data, _options):
        return None

    output_records: list[object] = []
    output_records_token = _yutto_output_records.set(output_records)
    try:
        with (
            patch(
                "blsync.consumer.yutto_wrapper._resolve_yutto_output_path",
                side_effect=RuntimeError("bad yutto shape"),
            ),
            patch(
                "blsync.consumer.yutto_wrapper._original_yutto_process_download",
                side_effect=fake_process_download,
            ),
        ):
            await _record_yutto_process_download(
                None,
                None,
                {"path": "episode"},
                {},
            )
    finally:
        _yutto_output_records.reset(output_records_token)

    assert output_records == [
        {
            "path": Path("episode"),
            "page_index": None,
            "page_cid": None,
            "page_name": None,
        }
    ]


@pytest.mark.asyncio
async def test_yutto_existing_output_file_skips_original_download(tmp_path):
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")

    output_records: list[object] = []
    output_records_token = _yutto_output_records.set(output_records)
    try:
        with (
            patch(
                "blsync.consumer.yutto_wrapper._resolve_yutto_output_path",
                return_value=video,
            ),
            patch(
                "blsync.consumer.yutto_wrapper._original_yutto_process_download",
            ) as original_process_download,
        ):
            result = await _record_yutto_process_download(
                None,
                None,
                {"path": "episode"},
                {"overwrite": False},
            )
    finally:
        _yutto_output_records.reset(output_records_token)

    assert result.name == "SKIP"
    assert output_records == [
        {"path": video, "page_index": None, "page_cid": None, "page_name": None}
    ]
    original_process_download.assert_not_called()


def test_yutto_downloader_uses_captured_progress_function():
    import yutto.downloader.downloader as yutto_downloader
    import yutto.downloader.progressbar as yutto_progressbar

    assert yutto_progressbar.show_progress is _capture_yutto_show_progress
    assert yutto_downloader.show_progress is _capture_yutto_show_progress


def test_yutto_downloader_uses_raising_ffmpeg_proxy():
    import yutto.downloader.downloader as yutto_downloader

    assert yutto_downloader.FFmpeg is _RaisingFFmpegProxy


def _fake_completed_process(returncode: int, stderr: bytes = b""):
    return SimpleNamespace(returncode=returncode, stderr=stderr)


def test_raising_ffmpeg_proxy_raises_on_nonzero_exit():
    inner = SimpleNamespace(
        exec=lambda _args: _fake_completed_process(
            1, b"Error closing file: No space left on device\nConversion failed!"
        ),
        path="/usr/bin/ffmpeg",
    )

    with patch(
        "blsync.consumer.yutto_wrapper._original_yutto_ffmpeg_class",
        return_value=inner,
    ):
        proxy = _RaisingFFmpegProxy()
        with pytest.raises(YuttoMergeFailedError, match="No space left on device"):
            proxy.exec(["-y", "out.mp4"])


def test_raising_ffmpeg_proxy_passes_through_success():
    success = _fake_completed_process(0, b"ok")
    inner = SimpleNamespace(exec=lambda _args: success, path="/usr/bin/ffmpeg")

    with patch(
        "blsync.consumer.yutto_wrapper._original_yutto_ffmpeg_class",
        return_value=inner,
    ):
        proxy = _RaisingFFmpegProxy()
        assert proxy.exec(["-version"]) is success
        # 非 exec 属性透传到内部实例
        assert proxy.path == "/usr/bin/ffmpeg"


@pytest.mark.asyncio
async def test_merge_failure_removes_truncated_output_and_reraises(tmp_path):
    truncated = tmp_path / "episode.mp4"
    truncated.write_bytes(b"partial")

    async def fake_process_download(_ctx, _client, _episode_data, _options):
        raise YuttoMergeFailedError("ffmpeg exited with code 1")

    output_records: list[object] = []
    output_records_token = _yutto_output_records.set(output_records)
    try:
        with (
            patch(
                "blsync.consumer.yutto_wrapper._resolve_yutto_output_path",
                return_value=truncated,
            ),
            patch(
                "blsync.consumer.yutto_wrapper._original_yutto_process_download",
                side_effect=fake_process_download,
            ),
        ):
            with pytest.raises(YuttoMergeFailedError):
                await _record_yutto_process_download(
                    None,
                    None,
                    {"path": "episode"},
                    {"overwrite": True},
                )
    finally:
        _yutto_output_records.reset(output_records_token)

    # 截断残骸被删除，避免重试时因“文件已存在”被跳过
    assert not truncated.exists()


@pytest.mark.asyncio
async def test_iter_download_video_progress_emits_failed_on_merge_error(tmp_path):
    async def fake_run(_args, _verbose, _callback, _bvid):
        raise YuttoMergeFailedError(
            "ffmpeg exited with code 1: Error closing file: No space left on device"
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
        ProgressEventType.FAILED,
    ]
    assert events[-1].status == "failed"
    assert "No space left on device" in (events[-1].message or "")


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
