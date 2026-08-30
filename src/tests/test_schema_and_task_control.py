"""Database migration, lease, and persisted task-control tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from blsync.db.task import BiliVideoTaskDAL, TaskStatus
from blsync.migrations import LATEST_SCHEMA_VERSION
from blsync.schema import SchemaMigrationError


@pytest.mark.asyncio
async def test_fresh_database_migrates_idempotently(tmp_path):
    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{tmp_path / 'fresh.sqlite3'}")
    try:
        assert await dal.migrate() == LATEST_SCHEMA_VERSION
        assert await dal.migrate() == LATEST_SCHEMA_VERSION
        async with dal.async_session() as session:
            version = await session.scalar(text("SELECT version FROM schema_version"))
            columns = (await session.execute(text("PRAGMA table_info(tasks)"))).all()
        assert version == LATEST_SCHEMA_VERSION
        assert {row[1] for row in columns} >= {
            "control_action",
            "worker_id",
            "lease_expires_at",
        }
    finally:
        await dal.close()


@pytest.mark.asyncio
async def test_legacy_database_is_preserved_and_upgraded(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{path}")
    try:
        async with dal.engine.begin() as conn:
            await conn.exec_driver_sql(
                """
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY, task_type VARCHAR(50) NOT NULL,
                    task_key VARCHAR(500) NOT NULL, task_data TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL, completed_at DATETIME,
                    error_message TEXT
                )
                """
            )
            await conn.exec_driver_sql(
                """INSERT INTO tasks
                (id, task_type, task_key, task_data, status, created_at, updated_at)
                VALUES (1, 'bili_video', '{}', '{}', 'paused', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP)"""
            )
        assert await dal.migrate() == LATEST_SCHEMA_VERSION
        task = await dal.get_task_by_id(1)
        assert task is not None
        assert task.status == TaskStatus.PAUSED.value
        assert task.control_action == "pause"
    finally:
        await dal.close()


@pytest.mark.asyncio
async def test_newer_database_version_is_rejected(tmp_path):
    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{tmp_path / 'newer.sqlite3'}")
    try:
        await dal.migrate()
        async with dal.engine.begin() as conn:
            await conn.exec_driver_sql(
                "UPDATE schema_version SET version = ? WHERE id = 1",
                (LATEST_SCHEMA_VERSION + 1,),
            )
        with pytest.raises(SchemaMigrationError, match="newer than supported"):
            await dal.migrate()
    finally:
        await dal.close()


@pytest.mark.asyncio
async def test_concurrent_startup_migration_is_serialized(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'startup.sqlite3'}"
    first = BiliVideoTaskDAL(url)
    second = BiliVideoTaskDAL(url)
    try:
        assert await asyncio.gather(first.migrate(), second.migrate()) == [
            LATEST_SCHEMA_VERSION,
            LATEST_SCHEMA_VERSION,
        ]
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_version(tmp_path, monkeypatch):
    from blsync import schema

    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{tmp_path / 'rollback.sqlite3'}")
    try:
        await dal.migrate()
        broken = SimpleNamespace(
            VERSION=LATEST_SCHEMA_VERSION + 1,
            STATEMENTS=(
                "ALTER TABLE tasks ADD COLUMN should_rollback INTEGER",
                "THIS IS NOT VALID SQL",
            ),
        )
        monkeypatch.setattr(schema, "MIGRATIONS", (*schema.MIGRATIONS, broken))
        monkeypatch.setattr(schema, "LATEST_SCHEMA_VERSION", LATEST_SCHEMA_VERSION + 1)
        with pytest.raises(Exception, match="syntax error"):
            await dal.migrate()
        async with dal.async_session() as session:
            version = await session.scalar(text("SELECT version FROM schema_version"))
        assert version == LATEST_SCHEMA_VERSION
    finally:
        await dal.close()


@pytest.mark.asyncio
async def test_atomic_claim_and_owner_guard(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'claim.sqlite3'}"
    first = BiliVideoTaskDAL(url)
    second = BiliVideoTaskDAL(url)
    try:
        await first.migrate()
        task = await first.create_bili_video_task("BV1", "fav", {})
        claims = await asyncio.gather(
            first.claim_ready_tasks("worker-a", 1, 30),
            second.claim_ready_tasks("worker-b", 1, 30),
        )
        claimed = [item for claim in claims for item in claim]
        assert [item.id for item in claimed] == [task.id]
        assert (
            await second.update_owned_task_status(
                task.id, "worker-b", TaskStatus.COMPLETED, release=True
            )
            is None
        )
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_pause_transition_and_resume_wait_for_owner(tmp_path):
    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{tmp_path / 'pause.sqlite3'}")
    try:
        await dal.migrate()
        task = await dal.create_bili_video_task("BV2", "fav", {})
        await dal.claim_ready_tasks("worker", 1, 30)
        await dal.update_owned_task_status(task.id, "worker", TaskStatus.DOWNLOADING)
        pausing = await dal.request_task_pause(task.id)
        assert pausing is not None and pausing.status == TaskStatus.PAUSING.value
        assert await dal.resume_paused_task(task.id) is None
        paused = await dal.update_owned_task_status(
            task.id, "worker", TaskStatus.PAUSED, release=True
        )
        assert paused is not None
        resumed = await dal.resume_paused_task(task.id)
        assert resumed is not None and resumed.status == TaskStatus.READY.value
    finally:
        await dal.close()


@pytest.mark.asyncio
async def test_expired_lease_is_recovered(tmp_path):
    dal = BiliVideoTaskDAL(f"sqlite+aiosqlite:///{tmp_path / 'lease.sqlite3'}")
    try:
        await dal.migrate()
        task = await dal.create_bili_video_task("BV3", "fav", {})
        await dal.claim_ready_tasks("dead-worker", 1, 30)
        async with dal.async_session() as session:
            await session.execute(
                text("UPDATE tasks SET lease_expires_at = :expired WHERE id = :id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "id": task.id},
            )
            await session.commit()
        assert await dal.recover_expired_tasks() == 1
        recovered = await dal.get_task_by_id(task.id)
        assert recovered is not None
        assert recovered.status == TaskStatus.READY.value
        assert recovered.worker_id is None
    finally:
        await dal.close()
