"""Routes for listing and serving files produced by completed tasks."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from blsync.consumer.bilibili import VIDEO_FILE_SUFFIXES
from blsync.db import get_task_dal, get_video_dal
from blsync.db.task import TaskModel, TaskStatus

router = APIRouter(tags=["文件"])

VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".flv": "video/x-flv",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


async def _task_downloaded_video_files(task: TaskModel) -> list[Path]:
    """Return recorded video files, preferring the download_files table."""
    records = await get_video_dal().get_files_by_task(task.id, file_type="video")
    files = [
        path
        for record in records
        if (path := record.absolute_path).exists() and path.is_file()
    ]
    if files:
        return files
    return downloaded_video_files_from_task_data(task.task_data)


def downloaded_video_files_from_task_data(task_data: str) -> list[Path]:
    """Read legacy downloaded file paths stored in a task_data JSON payload."""
    try:
        downloaded_files = json.loads(task_data).get("downloaded_files", [])
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(downloaded_files, list):
        return []

    files: list[Path] = []
    for path_value in downloaded_files:
        if not isinstance(path_value, str):
            continue

        path = Path(path_value)
        if path.suffix.lower() not in VIDEO_FILE_SUFFIXES:
            continue
        if path.exists() and path.is_file():
            files.append(path)

    return files


def task_file_summaries(task_id: int, files: list[Path]) -> list[dict[str, object]]:
    """Build the public file metadata returned by task endpoints."""
    return [
        {
            "index": index,
            "name": path.name,
            "size": path.stat().st_size,
            "download_url": f"/file/{task_id}/{index}",
        }
        for index, path in enumerate(files)
    ]


async def _get_completed_task(task_id: int) -> TaskModel:
    task = await get_task_dal().get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task.status != TaskStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail=f"Task {task_id} is not completed")
    return task


@router.get("/file/{task_id}", summary="获取任务下载文件列表")
async def get_task_files(task_id: int):
    """Return video files recorded for a completed task."""
    task = await _get_completed_task(task_id)
    files = await _task_downloaded_video_files(task)
    if not files:
        raise HTTPException(status_code=404, detail="No downloaded video files found")

    return {"task_id": task_id, "files": task_file_summaries(task_id, files)}


@router.get("/file/{task_id}/{file_index}", summary="下载任务视频文件")
async def download_task_file(task_id: int, file_index: int) -> FileResponse:
    """Download one recorded video file by task id and file index."""
    task = await _get_completed_task(task_id)
    files = await _task_downloaded_video_files(task)
    if file_index < 0 or file_index >= len(files):
        raise HTTPException(status_code=404, detail="Downloaded file not found")

    path = files[file_index]
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type=VIDEO_MEDIA_TYPES.get(
            path.suffix.lower(), "application/octet-stream"
        ),
        content_disposition_type="inline",
    )
