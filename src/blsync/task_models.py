"""Task database models using SQLAlchemy."""

import enum
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    Index,
    String,
    Text,
    delete,
    event,
    func,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TaskType(str, enum.Enum):
    """Task type enumeration."""

    BILI_VIDEO = "bili_video"
    # Add more task types here in the future


class TaskStatus(str, enum.Enum):
    """Task status enumeration."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class TaskModel(Base):
    """
    Task model for database storage.

    Attributes:
        id: Primary key
        task_type: Task type (e.g., 'bili_video')
        task_key: Unique task identifier in JSON format (e.g., '{"bvid":"BV1xx","favid":"123"}')
        task_data: Serialized task context (JSON)
        status: Task status
        created_at: Task creation timestamp
        updated_at: Last update timestamp
        completed_at: Task completion timestamp (optional)
        error_message: Error message if task failed (optional)
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(String(50))
    task_key: Mapped[str] = mapped_column(String(500))
    task_data: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)

    # Unique index on task_key and regular indexes for common queries
    __table_args__ = (
        Index("ix_tasks_task_key", "task_key", unique=True),
        Index("ix_tasks_task_type", "task_type"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )

    @property
    def key_dict(self) -> dict[str, str]:
        """Parse task_key JSON to dictionary."""
        return json.loads(self.task_key)

    @property
    def task_context_dict(self) -> dict[str, Any]:
        """Deserialize task_data JSON to dictionary."""
        return json.loads(self.task_data)

    @classmethod
    def from_task_context(
        cls,
        task_type: TaskType,
        task_key: dict[str, str],
        task_context: dict[str, Any],
    ) -> "TaskModel":
        """
        Create a TaskModel from a task context dictionary.

        Args:
            task_type: Type of the task
            task_key: Unique identifier dict (e.g., {"bvid": "BV1xx", "favid": "123"})
            task_context: Task context dictionary
        """
        return cls(
            task_type=task_type.value,
            task_key=json.dumps(task_key, sort_keys=True),
            task_data=json.dumps(task_context),
            status=TaskStatus.PENDING.value,
        )

    @classmethod
    def create_bili_video_task(
        cls,
        bvid: str,
        favid: str,
        task_context: dict[str, Any],
    ) -> "TaskModel":
        """
        Helper to create a Bilibili video task.

        Args:
            bvid: Video ID
            favid: Favorite list ID
            task_context: Task context dictionary
        """
        return cls.from_task_context(
            task_type=TaskType.BILI_VIDEO,
            task_key={"bvid": bvid, "favid": favid},
            task_context=task_context,
        )


def make_bili_video_key(bvid: str, favid: str) -> str:
    """
    Create a task_key JSON string for Bilibili video tasks.

    Args:
        bvid: Video ID
        favid: Favorite list ID

    Returns:
        JSON string representing the task key
    """
    return json.dumps({"bvid": bvid, "favid": favid}, sort_keys=True)


def parse_bili_video_key(task_key: str) -> tuple[str, str]:
    """
    Parse a Bilibili video task_key JSON string.

    Args:
        task_key: JSON string from task_key field

    Returns:
        Tuple of (bvid, favid)
    """
    key_dict = json.loads(task_key)
    return key_dict["bvid"], key_dict["favid"]


class TaskDAL:
    """
    Data Access Layer for Task operations.

    Provides async methods for CRUD operations on tasks.
    This is the base class with common task operations.
    """

    def __init__(self, db_url: str = "sqlite+aiosqlite:///:memory:"):
        """
        Initialize the Task Data Access Layer.

        Args:
            db_url: Database URL for SQLAlchemy connection
        """
        self.db_url = db_url
        self.engine = create_async_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
        )

        # Set up SQLite PRAGMA commands on new connections
        if db_url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _connection_record):
                """Set SQLite PRAGMA commands on new connections."""
                cursor = dbapi_conn.cursor()
                try:
                    # Enable WAL mode for better concurrency
                    cursor.execute("PRAGMA journal_mode=WAL;")
                    # Enable foreign keys
                    cursor.execute("PRAGMA foreign_keys=ON;")
                    # Set busy timeout to 20 seconds
                    cursor.execute("PRAGMA busy_timeout=20000;")
                except Exception:
                    pass  # Ignore errors for non-SQLite databases

        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self):
        """Create all database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self):
        """Drop all database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.async_session()

    async def get_task_by_key(self, task_key: str) -> TaskModel | None:
        """
        Get a task by its unique key.

        Args:
            task_key: Task key JSON string

        Returns:
            TaskModel instance if found, None otherwise
        """
        async with self.async_session() as session:
            stmt = select(TaskModel).where(TaskModel.task_key == task_key)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_task_status(
        self,
        task_key: str,
        status: TaskStatus,
        error_message: str | None = None,
    ) -> TaskModel | None:
        """
        Update task status using native async update statement.

        Args:
            task_key: Task key JSON string
            status: New task status
            error_message: Optional error message for failed tasks

        Returns:
            Updated TaskModel instance if found, None otherwise
        """
        async with self.async_session() as session:
            # Build update values
            values: dict[str, Any] = {"status": status.value}
            if status == TaskStatus.COMPLETED:
                values["completed_at"] = datetime.now(timezone.utc)
                values["error_message"] = None
            elif status == TaskStatus.FAILED:
                values["error_message"] = error_message

            # Execute update statement
            stmt = (
                update(TaskModel)
                .where(TaskModel.task_key == task_key)
                .values(**values)
                .returning(TaskModel)
            )
            result = await session.execute(stmt)
            await session.commit()

            return result.scalars().first()

    async def get_tasks_by_status(self, status: TaskStatus) -> list[TaskModel]:
        """
        Get all tasks with a specific status.

        Args:
            status: Task status to filter by

        Returns:
            List of TaskModel instances
        """
        async with self.async_session() as session:
            stmt = select(TaskModel).where(TaskModel.status == status.value)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_pending_tasks(self, limit: int | None = None) -> list[TaskModel]:
        """
        Get pending tasks, optionally limited.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of pending TaskModel instances
        """
        async with self.async_session() as session:
            stmt = (
                select(TaskModel)
                .where(TaskModel.status == TaskStatus.PENDING.value)
                .order_by(TaskModel.created_at)
            )
            if limit:
                stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_task_stats(self) -> dict[str, int]:
        """
        Get task statistics.

        Returns:
            Dictionary with task counts by status
        """
        async with self.async_session() as session:
            stats = {status.value: 0 for status in TaskStatus}

            for status in TaskStatus:
                stmt = select(func.count(TaskModel.id)).where(
                    TaskModel.status == status.value
                )
                result = await session.execute(stmt)
                stats[status.value] = result.scalar() or 0

            return stats

    async def get_tasks_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> dict:
        """
        Get paginated task list with optional status filter.

        Uses raw SQL to bypass SQLAlchemy's type cache issues.

        Args:
            page: Page number (1-indexed)
            page_size: Number of items per page
            status: Optional status filter

        Returns:
            Dictionary with 'items', 'total', 'page', 'page_size' keys
        """
        async with self.async_session() as session:
            # Build WHERE clause for status filter
            where_clause = ""
            params = {"offset": (page - 1) * page_size, "limit": page_size}
            if status:
                where_clause = "WHERE status = :status"
                params["status"] = status

            # Get total count
            count_sql = text(f"SELECT COUNT(*) FROM tasks {where_clause}")
            count_result = await session.execute(count_sql, params)
            total = count_result.scalar() or 0

            # Get paginated results
            query_sql = text(f"""
                SELECT id, task_type, task_key, task_data, status,
                       created_at, updated_at, completed_at, error_message
                FROM tasks
                {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """)
            result = await session.execute(query_sql, params)
            rows = result.all()

            # Convert rows to dict (raw SQL returns strings for dates)
            items = []
            for row in rows:
                items.append({
                    "id": row[0],
                    "task_type": row[1],
                    "task_key": row[2],
                    "task_data": row[3],
                    "status": row[4],
                    "created_at": row[5] if row[5] else None,  # Already a string from SQLite
                    "updated_at": row[6] if row[6] else None,
                    "completed_at": row[7] if row[7] else None,
                    "error_message": row[8],
                })

            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def _task_to_dict(self, task: TaskModel) -> dict:
        """Convert TaskModel to dictionary."""
        return {
            "id": task.id,
            "task_type": task.task_type,
            "task_key": task.task_key,
            "task_data": task.task_data,
            "status": task.status,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "error_message": task.error_message,
        }

    async def delete_task(self, task_key: str) -> bool:
        """
        Delete a task by its unique key using native async delete statement.

        Args:
            task_key: Task key JSON string

        Returns:
            True if task was deleted, False if not found
        """
        async with self.async_session() as session:
            stmt = (
                delete(TaskModel)
                .where(TaskModel.task_key == task_key)
                .returning(TaskModel.id)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalar_one_or_none() is not None

    async def close(self):
        """Close the database connection."""
        await self.engine.dispose()


class BiliVideoTaskDAL(TaskDAL):
    """
    Data Access Layer for Bilibili Video tasks.

    Extends TaskDAL with Bilibili video-specific operations.
    """

    async def create_bili_video_task(
        self,
        bvid: str,
        favid: str,
        task_context: dict[str, Any],
    ) -> TaskModel:
        """
        Create a new Bilibili video task.

        Args:
            bvid: Video ID
            favid: Favorite list ID
            task_context: Task context dictionary

        Returns:
            Created TaskModel instance
        """
        async with self.async_session() as session:
            task = TaskModel.create_bili_video_task(bvid, favid, task_context)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def has_bili_video_task(self, bvid: str, favid: str) -> bool:
        """
        Check if a Bilibili video task exists.

        Args:
            bvid: Video ID
            favid: Favorite list ID

        Returns:
            True if task exists, False otherwise
        """
        task_key = make_bili_video_key(bvid, favid)
        task = await self.get_task_by_key(task_key)
        return task is not None

    async def get_bili_video_task_status(
        self, bvid: str, favid: str
    ) -> TaskStatus | None:
        """
        Get the status of a Bilibili video task.

        Args:
            bvid: Video ID
            favid: Favorite list ID

        Returns:
            TaskStatus if task exists, None otherwise
        """
        task_key = make_bili_video_key(bvid, favid)
        task = await self.get_task_by_key(task_key)
        if task is None:
            return None
        return TaskStatus(task.status)

    async def delete_stale_tasks(
        self, downloaded_bvids: set[str], favid: str
    ) -> list[tuple[str, str]]:
        """
        Delete Bilibili video tasks that are already downloaded.

        Args:
            downloaded_bvids: Set of downloaded video IDs
            favid: Favorite list ID

        Returns:
            List of deleted task keys (bvid, favid)
        """
        deleted_keys = []

        async with self.async_session() as session:
            # Get all pending or executing Bilibili video tasks
            stmt = select(TaskModel).where(
                TaskModel.task_type == TaskType.BILI_VIDEO.value,
                TaskModel.status.in_(
                    [TaskStatus.PENDING.value, TaskStatus.EXECUTING.value]
                ),
            )
            result = await session.execute(stmt)
            tasks = list(result.scalars().all())

            for task in tasks:
                key_dict = task.key_dict
                if (
                    key_dict.get("favid") == favid
                    and key_dict.get("bvid") in downloaded_bvids
                ):
                    deleted_keys.append((key_dict["bvid"], key_dict["favid"]))
                    await session.delete(task)

            await session.commit()

        return deleted_keys

    async def get_completed_bvids(self, favid: str) -> set[str]:
        """
        Get all bvids for completed tasks in a favorite list.

        Args:
            favid: Favorite list ID

        Returns:
            Set of completed bvids
        """
        async with self.async_session() as session:
            stmt = select(TaskModel).where(
                TaskModel.task_type == TaskType.BILI_VIDEO.value,
                TaskModel.status == TaskStatus.COMPLETED.value,
            )
            result = await session.execute(stmt)
            tasks = list(result.scalars().all())

            # Filter by favid and extract bvids
            return {task.key_dict["bvid"] for task in tasks if task.key_dict.get("favid") == favid}

    async def update_bili_video_task(
        self,
        bvid: str,
        favid: str,
        task_context: dict[str, Any],
        reset_status: bool = False,
    ) -> TaskModel | None:
        """
        Update a Bilibili video task's context.

        Args:
            bvid: Video ID
            favid: Favorite list ID
            task_context: New task context dictionary
            reset_status: If True, reset status to PENDING (useful for retrying failed tasks)

        Returns:
            Updated TaskModel instance if found, None otherwise
        """
        task_key = make_bili_video_key(bvid, favid)
        async with self.async_session() as session:
            stmt = select(TaskModel).where(TaskModel.task_key == task_key)
            result = await session.execute(stmt)
            task = result.scalar_one_or_none()

            if task is None:
                return None

            task.task_data = json.dumps(task_context)
            if reset_status:
                task.status = TaskStatus.PENDING.value
                task.error_message = None

            await session.commit()
            await session.refresh(task)
            return task
