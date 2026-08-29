"""Test video metadata and download file ORM models."""

import json

import pytest
from sqlalchemy import text

from blsync.db.task import BiliVideoTaskDAL
from blsync.db.video import VideoDAL


def _make_video_info(
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
    pages: list[dict] | None = None,
) -> dict:
    if pages is None:
        pages = [{"cid": 100, "page": 1, "part": "P1", "duration": 300, "desc": ""}]
    return {
        "bvid": bvid,
        "aid": 12345,
        "title": title,
        "desc": "视频简介",
        "pic": "http://example.com/pic.jpg",
        "pubdate": 1700000000,
        "duration": 300,
        "videos": len(pages),
        "owner": {
            "mid": 666,
            "name": "测试UP主",
            "face": "http://example.com/face.jpg",
        },
        "stat": {
            "view": 1000,
            "danmaku": 10,
            "reply": 20,
            "favorite": 30,
            "coin": 40,
            "share": 50,
            "like": 60,
        },
        "pages": pages,
    }


@pytest.mark.asyncio
async def test_migration_creates_video_tables():
    """Test that migrations create the video metadata tables."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    async with dal.engine.begin() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        tables = {row[0] for row in result.all()}

    assert {"videos", "video_pages", "download_files"} <= tables

    await dal.close()


@pytest.mark.asyncio
async def test_upsert_video_info_creates_video_and_pages():
    """Test creating video metadata and pages from raw video info."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()
    video_dal = VideoDAL(dal.async_session)

    pages = [
        {"cid": 100, "page": 1, "part": "P1", "duration": 300, "desc": ""},
        {"cid": 101, "page": 2, "part": "P2", "duration": 240, "desc": ""},
    ]
    video = await video_dal.upsert_video_info(
        _make_video_info(pages=pages), tags=["教程", "编程"]
    )

    assert video.id is not None
    assert video.bvid == "BV1xx411c7mD"
    assert video.aid == 12345
    assert video.title == "测试视频"
    assert video.owner_mid == 666
    assert video.owner_name == "测试UP主"
    assert video.videos_count == 2
    assert video.stat_likes == 60
    assert video.stat_coins == 40
    assert video.stat_favorites == 30
    assert video.stat_shares == 50
    assert video.tag_list == ["教程", "编程"]
    assert json.loads(video.raw_info)["bvid"] == "BV1xx411c7mD"

    stored = await video_dal.get_video_by_bvid("BV1xx411c7mD")
    assert stored is not None and stored.id == video.id

    video_pages = await video_dal.get_video_pages(video.id)
    assert [page.page_index for page in video_pages] == [1, 2]
    assert video_pages[0].cid == 100
    assert video_pages[1].title == "P2"

    await dal.close()


@pytest.mark.asyncio
async def test_upsert_video_info_updates_and_preserves_page_ids():
    """Test that re-upserting updates fields without duplicating pages."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()
    video_dal = VideoDAL(dal.async_session)

    pages = [
        {"cid": 100, "page": 1, "part": "P1", "duration": 300, "desc": ""},
        {"cid": 101, "page": 2, "part": "P2", "duration": 240, "desc": ""},
    ]
    video = await video_dal.upsert_video_info(_make_video_info(pages=pages))
    old_page_ids = [page.id for page in await video_dal.get_video_pages(video.id)]

    updated_pages = [
        {"cid": 100, "page": 1, "part": "P1改", "duration": 301, "desc": ""},
        {"cid": 101, "page": 2, "part": "P2", "duration": 240, "desc": ""},
    ]
    updated = await video_dal.upsert_video_info(
        _make_video_info(title="新标题", pages=updated_pages),
        tags=["新标签"],
    )

    assert updated.id == video.id
    assert updated.title == "新标题"
    assert updated.tag_list == ["新标签"]

    video_pages = await video_dal.get_video_pages(video.id)
    assert len(video_pages) == 2
    assert [page.id for page in video_pages] == old_page_ids
    assert video_pages[0].title == "P1改"
    assert video_pages[0].duration == 301

    await dal.close()


@pytest.mark.asyncio
async def test_replace_and_get_task_files():
    """Test recording and querying download files by task."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()
    video_dal = VideoDAL(dal.async_session)

    task = await dal.create_bili_video_task(
        "BV1xx411c7mD", "fav123", {"bid": "BV1xx411c7mD", "task_name": "fav123"}
    )
    video = await video_dal.upsert_video_info(_make_video_info())
    pages = await video_dal.get_video_pages(video.id)

    files = [
        {
            "file_type": "video",
            "file_path": "/data/sync/测试视频.mp4",
            "file_size": 1024,
            "page_id": pages[0].id,
        },
        {
            "file_type": "cover",
            "file_path": "/data/sync/测试视频-poster.jpg",
            "file_size": 256,
            "page_id": pages[0].id,
        },
        {
            "file_type": "metadata",
            "file_path": "/data/sync/测试视频.nfo",
            "file_size": 128,
            "page_id": None,
        },
    ]
    records = await video_dal.replace_task_files(
        task_id=task.id,
        video_id=video.id,
        files=files,
    )
    assert len(records) == 3
    assert all(record.task_id == task.id for record in records)
    assert [record.page_id for record in records] == [
        pages[0].id,
        pages[0].id,
        None,
    ]

    video_files = await video_dal.get_files_by_task(task.id, file_type="video")
    assert len(video_files) == 1
    assert video_files[0].file_path == "/data/sync/测试视频.mp4"
    assert video_files[0].file_size == 1024

    files_by_task = await video_dal.get_files_by_tasks([task.id], file_type="video")
    assert [f.id for f in files_by_task[task.id]] == [video_files[0].id]

    # 同一任务重复下载：按 task_id 整体替换
    await video_dal.replace_task_files(
        task_id=task.id,
        video_id=video.id,
        files=[
            {
                "file_type": "video",
                "file_path": "/data/sync/测试视频.mp4",
                "file_size": 2048,
                "page_id": pages[0].id,
            },
        ],
    )
    replaced = await video_dal.get_files_by_task(task.id)
    assert len(replaced) == 1
    assert replaced[0].file_size == 2048

    await dal.close()
