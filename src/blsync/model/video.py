"""Video metadata and download file models using SQLAlchemy."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from blsync.model.task import Base


class VideoModel(Base):
    """
    Video metadata model, one row per BVID.

    Attributes:
        id: Primary key
        bvid: Bilibili video ID (unique)
        aid: Bilibili AV ID
        title: Video title
        description: Video description
        pic: Cover image URL
        pubdate: Publish timestamp
        duration: Total duration in seconds
        videos_count: Number of pages (分P)
        owner_mid: Uploader user ID
        owner_name: Uploader name
        owner_face: Uploader avatar URL
        tags: JSON array of tag names
        stat_*: Snapshot of engagement counters
        raw_info: Raw JSON returned by bilibili_api
        created_at: Row creation timestamp
        updated_at: Row update timestamp
    """

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    bvid: Mapped[str] = mapped_column(String(50))
    aid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pubdate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    videos_count: Mapped[int] = mapped_column(Integer, default=1)
    owner_mid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_face: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text(), nullable=True)
    stat_likes: Mapped[int] = mapped_column(Integer, default=0)
    stat_coins: Mapped[int] = mapped_column(Integer, default=0)
    stat_favorites: Mapped[int] = mapped_column(Integer, default=0)
    stat_shares: Mapped[int] = mapped_column(Integer, default=0)
    stat_views: Mapped[int] = mapped_column(Integer, default=0)
    stat_danmakus: Mapped[int] = mapped_column(Integer, default=0)
    stat_replies: Mapped[int] = mapped_column(Integer, default=0)
    raw_info: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_videos_bvid", "bvid", unique=True),
        Index("ix_videos_owner_mid", "owner_mid"),
    )

    @property
    def tag_list(self) -> list[str]:
        """Parse tags JSON to a list of tag names."""
        if not self.tags:
            return []
        try:
            tags = json.loads(self.tags)
        except (TypeError, json.JSONDecodeError):
            return []
        return list(tags) if isinstance(tags, list) else []


class VideoPageModel(Base):
    """
    Video page (分P) model, one row per page of a video.

    Attributes:
        id: Primary key
        video_id: Owning video id
        cid: Page cid
        page_index: Page index (1-based)
        title: Page title
        duration: Page duration in seconds
        description: Page description
        created_at: Row creation timestamp
        updated_at: Row update timestamp
    """

    __tablename__ = "video_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE")
    )
    cid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_video_pages_video_page", "video_id", "page_index", unique=True),
    )


class DownloadFileModel(Base):
    """
    Downloaded file model, one row per final output file.

    Attributes:
        id: Primary key
        task_id: Task that produced the file (optional)
        video_id: Owning video id
        page_id: Owning page id (only set for single-page videos)
        file_type: File type (video/audio/cover/metadata/subtitle/danmaku)
        file_path: Absolute path of the final file on disk
        file_size: File size in bytes
        created_at: Row creation timestamp
        updated_at: Row update timestamp
    """

    __tablename__ = "download_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE")
    )
    page_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_pages.id", ondelete="CASCADE"), nullable=True
    )
    file_type: Mapped[str] = mapped_column(String(20))
    file_path: Mapped[str] = mapped_column(Text())
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_download_files_task_id", "task_id"),
        Index("ix_download_files_video_id", "video_id"),
    )


class VideoDAL:
    """
    Data Access Layer for video metadata and download files.

    Shares the database connection with the task DAL by accepting
    its ``async_session`` factory.
    """

    def __init__(self, async_session: async_sessionmaker[AsyncSession]):
        """
        Initialize the Video Data Access Layer.

        Args:
            async_session: Session factory shared with the task DAL
        """
        self.async_session = async_session

    async def upsert_video_info(
        self,
        info: dict[str, Any],
        tags: list[str] | None = None,
    ) -> VideoModel:
        """
        Create or update a video (and its pages) from bilibili_api info.

        Pages are upserted one by one keyed on (video_id, page_index) so
        existing page ids stay stable for download file references.

        Args:
            info: Raw dict returned by ``Video.get_info()``
            tags: Optional list of tag names

        Returns:
            The upserted VideoModel instance
        """
        bvid = info.get("bvid")
        if not bvid:
            raise ValueError("video info must contain bvid")

        owner = info.get("owner") or {}
        stat = info.get("stat") or {}
        values: dict[str, Any] = {
            "aid": info.get("aid"),
            "title": info.get("title") or "",
            "description": info.get("desc"),
            "pic": info.get("pic"),
            "pubdate": info.get("pubdate"),
            "duration": info.get("duration"),
            "videos_count": info.get("videos", 1),
            "owner_mid": owner.get("mid"),
            "owner_name": owner.get("name"),
            "owner_face": owner.get("face"),
            "tags": json.dumps(tags, ensure_ascii=False) if tags else None,
            "stat_likes": stat.get("like") or 0,
            "stat_coins": stat.get("coin") or 0,
            "stat_favorites": stat.get("favorite") or 0,
            "stat_shares": stat.get("share") or 0,
            "stat_views": stat.get("view") or 0,
            "stat_danmakus": stat.get("danmaku") or 0,
            "stat_replies": stat.get("reply") or 0,
            "raw_info": json.dumps(info, ensure_ascii=False),
        }

        async with self.async_session() as session:
            stmt = select(VideoModel).where(VideoModel.bvid == bvid)
            video = (await session.execute(stmt)).scalar_one_or_none()

            if video is None:
                video = VideoModel(bvid=bvid, **values)
                session.add(video)
                await session.flush()
            else:
                for key, value in values.items():
                    setattr(video, key, value)

            existing_pages = {
                page.page_index: page
                for page in (
                    await session.execute(
                        select(VideoPageModel).where(
                            VideoPageModel.video_id == video.id
                        )
                    )
                ).scalars()
            }
            for page_info in info.get("pages") or []:
                page_index = page_info.get("page")
                if page_index is None:
                    continue
                page = existing_pages.get(page_index)
                if page is None:
                    session.add(
                        VideoPageModel(
                            video_id=video.id,
                            page_index=page_index,
                            cid=page_info.get("cid"),
                            title=page_info.get("part") or "",
                            duration=page_info.get("duration"),
                            description=page_info.get("desc"),
                        )
                    )
                else:
                    page.cid = page_info.get("cid")
                    page.title = page_info.get("part") or ""
                    page.duration = page_info.get("duration")
                    page.description = page_info.get("desc")

            await session.commit()
            await session.refresh(video)
            return video

    async def get_video_by_bvid(self, bvid: str) -> VideoModel | None:
        """Get a video by its BVID."""
        async with self.async_session() as session:
            stmt = select(VideoModel).where(VideoModel.bvid == bvid)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_videos_by_bvids(
        self, bvids: list[str]
    ) -> dict[str, VideoModel]:
        """
        Get multiple videos by their BVIDs in one query.

        Args:
            bvids: List of BVIDs

        Returns:
            Mapping of bvid to VideoModel for the ones found
        """
        if not bvids:
            return {}
        async with self.async_session() as session:
            stmt = select(VideoModel).where(VideoModel.bvid.in_(bvids))
            result = await session.execute(stmt)
            return {video.bvid: video for video in result.scalars()}

    async def get_video_pages(self, video_id: int) -> list[VideoPageModel]:
        """Get all pages of a video ordered by page index."""
        async with self.async_session() as session:
            stmt = (
                select(VideoPageModel)
                .where(VideoPageModel.video_id == video_id)
                .order_by(VideoPageModel.page_index)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def replace_task_files(
        self,
        *,
        task_id: int | None,
        video_id: int,
        files: list[dict[str, Any]],
    ) -> list[DownloadFileModel]:
        """
        Replace the download file records produced by one task.

        Existing records for the same task (or the same video when no task
        id is given) are deleted before inserting the new ones.

        Args:
            task_id: Task that produced the files (optional)
            video_id: Owning video id
            files: List of dicts with file_type, file_path, file_size and
                page_id keys

        Returns:
            The inserted DownloadFileModel instances
        """
        async with self.async_session() as session:
            if task_id is not None:
                await session.execute(
                    delete(DownloadFileModel).where(
                        DownloadFileModel.task_id == task_id
                    )
                )
            else:
                await session.execute(
                    delete(DownloadFileModel).where(
                        DownloadFileModel.video_id == video_id
                    )
                )

            records: list[DownloadFileModel] = []
            for file in files:
                record = DownloadFileModel(
                    task_id=task_id,
                    video_id=video_id,
                    page_id=file.get("page_id"),
                    file_type=file["file_type"],
                    file_path=file["file_path"],
                    file_size=file.get("file_size"),
                )
                session.add(record)
                records.append(record)

            await session.commit()
            for record in records:
                await session.refresh(record)
            return records

    async def get_files_by_task(
        self, task_id: int, file_type: str | None = None
    ) -> list[DownloadFileModel]:
        """
        Get download files recorded for one task.

        Args:
            task_id: Task id
            file_type: Optional file type filter (e.g. 'video')

        Returns:
            List of DownloadFileModel instances ordered by id
        """
        async with self.async_session() as session:
            stmt = (
                select(DownloadFileModel)
                .where(DownloadFileModel.task_id == task_id)
                .order_by(DownloadFileModel.id)
            )
            if file_type is not None:
                stmt = stmt.where(DownloadFileModel.file_type == file_type)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_files_by_tasks(
        self, task_ids: list[int], file_type: str | None = None
    ) -> dict[int, list[DownloadFileModel]]:
        """
        Get download files recorded for multiple tasks in one query.

        Args:
            task_ids: Task ids
            file_type: Optional file type filter (e.g. 'video')

        Returns:
            Mapping of task id to its DownloadFileModel instances
        """
        if not task_ids:
            return {}
        async with self.async_session() as session:
            stmt = (
                select(DownloadFileModel)
                .where(DownloadFileModel.task_id.in_(task_ids))
                .order_by(DownloadFileModel.id)
            )
            if file_type is not None:
                stmt = stmt.where(DownloadFileModel.file_type == file_type)
            result = await session.execute(stmt)
            files_by_task: dict[int, list[DownloadFileModel]] = {}
            for record in result.scalars():
                if record.task_id is not None:
                    files_by_task.setdefault(record.task_id, []).append(record)
            return files_by_task
