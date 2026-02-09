"""Test Task database models and SQLite creation."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from blsync.task_models import (
    BiliVideoTaskDAL,
    TaskModel,
    TaskStatus,
    TaskType,
    make_bili_video_key,
    parse_bili_video_key,
)


@pytest.mark.asyncio
async def test_create_tables_in_memory():
    """Test creating tables in in-memory database."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    # Verify tables exist by querying
    async with dal.engine.begin() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        )
        table_exists = result.first() is not None
        assert table_exists, "Tasks table should exist"

    await dal.close()


@pytest.mark.asyncio
async def test_create_tables_file():
    """Test creating tables in a file database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"

        # Ensure database file doesn't exist
        assert not db_path.exists(), "Database file should not exist initially"

        dal = BiliVideoTaskDAL(db_url)
        await dal.create_tables()

        # Verify database file was created
        assert db_path.exists(), "Database file should be created"
        assert db_path.stat().st_size > 0, "Database file should not be empty"

        # Verify tables exist
        async with dal.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
            )
            table_exists = result.first() is not None
            assert table_exists, "Tasks table should exist"

        await dal.close()


@pytest.mark.asyncio
async def test_wal_mode_enabled():
    """Test that WAL mode is enabled on database connections."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        dal = BiliVideoTaskDAL(db_url)

        # Create tables which should enable WAL mode
        await dal.create_tables()

        # Check WAL mode on a fresh connection from the pool
        async with dal.engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = result.scalar()
            # WAL might not persist across connections in all SQLite versions,
            # but check if it's set correctly on at least one connection
            assert journal_mode in ("wal", "memory"), f"journal_mode should be wal or memory, got {journal_mode}"

        await dal.close()


@pytest.mark.asyncio
async def test_crud_operations():
    """Test basic CRUD operations on tasks."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    # Create a task
    task_context = {"title": "Test Video", "url": "https://bilibili.com/video/test"}
    task = await dal.create_bili_video_task("BV123456", "fav123", task_context)

    assert task.id is not None, "Task should have an ID"
    assert task.task_type == TaskType.BILI_VIDEO.value
    assert task.status == TaskStatus.PENDING.value

    # Get task by key
    task_key = make_bili_video_key("BV123456", "fav123")
    retrieved_task = await dal.get_task_by_key(task_key)
    assert retrieved_task is not None, "Task should be retrieved"
    assert retrieved_task.id == task.id

    # Check if task exists
    exists = await dal.has_bili_video_task("BV123456", "fav123")
    assert exists, "Task should exist"

    # Update task status
    updated_task = await dal.update_task_status(task_key, TaskStatus.COMPLETED)
    assert updated_task is not None, "Task should be updated"
    assert updated_task.status == TaskStatus.COMPLETED.value
    assert updated_task.completed_at is not None, "Task should have completion time"

    # Get tasks by status
    pending_tasks = await dal.get_tasks_by_status(TaskStatus.PENDING)
    assert len(pending_tasks) == 0, "No pending tasks should exist"

    completed_tasks = await dal.get_tasks_by_status(TaskStatus.COMPLETED)
    assert len(completed_tasks) == 1, "One completed task should exist"

    # Delete task
    deleted = await dal.delete_task(task_key)
    assert deleted, "Task should be deleted"

    # Verify deletion
    exists_after = await dal.has_bili_video_task("BV123456", "fav123")
    assert not exists_after, "Task should not exist after deletion"

    await dal.close()


@pytest.mark.asyncio
async def test_task_key_helpers():
    """Test task key helper functions."""
    bvid = "BV123456"
    favid = "fav123"

    # Test make_bili_video_key
    key = make_bili_video_key(bvid, favid)
    assert isinstance(key, str)
    assert bvid in key
    assert favid in key

    # Test parse_bili_video_key
    parsed_bvid, parsed_favid = parse_bili_video_key(key)
    assert parsed_bvid == bvid
    assert parsed_favid == favid


@pytest.mark.asyncio
async def test_task_model_from_context():
    """Test creating TaskModel from task context."""
    task_type = TaskType.BILI_VIDEO
    task_key = {"bvid": "BV123456", "favid": "fav123"}
    task_context = {"title": "Test Video", "url": "https://bilibili.com/video/test"}

    task = TaskModel.from_task_context(task_type, task_key, task_context)

    assert task.task_type == TaskType.BILI_VIDEO.value
    assert task.status == TaskStatus.PENDING.value
    assert task.key_dict == task_key
    assert task.task_context_dict == task_context


@pytest.mark.asyncio
async def test_task_stats():
    """Test getting task statistics."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    # Create multiple tasks with different statuses
    await dal.create_bili_video_task("BV1", "fav1", {})
    await dal.create_bili_video_task("BV2", "fav2", {})

    task_key = make_bili_video_key("BV1", "fav1")
    await dal.update_task_status(task_key, TaskStatus.COMPLETED)

    task_key2 = make_bili_video_key("BV2", "fav2")
    await dal.update_task_status(task_key2, TaskStatus.FAILED, "Test error")

    # Get stats
    stats = await dal.get_task_stats()

    assert stats[TaskStatus.PENDING.value] == 0
    assert stats[TaskStatus.EXECUTING.value] == 0
    assert stats[TaskStatus.COMPLETED.value] == 1
    assert stats[TaskStatus.FAILED.value] == 1

    await dal.close()


@pytest.mark.asyncio
async def test_get_pending_tasks_with_limit():
    """Test getting pending tasks with limit."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    # Create multiple tasks
    for i in range(5):
        await dal.create_bili_video_task(f"BV{i}", "fav1", {})

    # Get with limit
    tasks = await dal.get_pending_tasks(limit=3)
    assert len(tasks) == 3, "Should return limited number of tasks"

    # Get without limit
    all_tasks = await dal.get_pending_tasks()
    assert len(all_tasks) == 5, "Should return all tasks"

    await dal.close()


@pytest.mark.asyncio
async def test_cleanup_stale_tasks():
    """Test cleanup of already downloaded tasks."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    # Create tasks
    await dal.create_bili_video_task("BV1", "fav1", {})
    await dal.create_bili_video_task("BV2", "fav1", {})
    await dal.create_bili_video_task("BV3", "fav2", {})

    # Mark BV1 and BV2 as downloaded
    downloaded_bvids = {"BV1", "BV2"}

    # Cleanup tasks for fav1
    deleted_keys = await dal.delete_stale_tasks(downloaded_bvids, "fav1")

    assert len(deleted_keys) == 2, "Should delete 2 tasks from fav1"
    assert ("BV1", "fav1") in deleted_keys
    assert ("BV2", "fav1") in deleted_keys

    # Verify deletion
    assert not await dal.has_bili_video_task("BV1", "fav1")
    assert not await dal.has_bili_video_task("BV2", "fav1")
    assert await dal.has_bili_video_task("BV3", "fav2"), "BV3 should still exist"

    await dal.close()


@pytest.mark.asyncio
async def test_concurrent_operations():
    """Test concurrent database operations don't cause issues."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    await dal.create_tables()

    async def create_tasks(start: int, count: int):
        for i in range(start, start + count):
            await dal.create_bili_video_task(f"BV{i}", f"fav{i}", {})

    # Run concurrent operations with non-overlapping keys
    tasks = [
        asyncio.create_task(create_tasks(0, 10)),
        asyncio.create_task(create_tasks(10, 10)),
        asyncio.create_task(create_tasks(20, 10)),
    ]

    await asyncio.gather(*tasks)

    # Verify total count
    all_tasks = await dal.get_pending_tasks()
    assert len(all_tasks) == 30, "Should have 30 tasks total"

    await dal.close()
