"""Download progress event types and in-memory fan-out helpers."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from enum import StrEnum


class ProgressEventType(StrEnum):
    STATUS = "status"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class DownloadProgressEvent:
    event: ProgressEventType
    task_id: int | None
    bvid: str
    status: str
    overall_percent: float | None = None
    episode_index: int | None = None
    episode_count: int | None = None
    episode_name: str | None = None
    episode_percent: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TaskProgressBroker:
    """In-memory single-task progress broadcaster with latest snapshots."""

    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[DownloadProgressEvent]]] = {}
        self._global_subscribers: set[asyncio.Queue[DownloadProgressEvent]] = set()
        self._latest: dict[int, DownloadProgressEvent] = {}

    def publish(self, task_id: int, event: DownloadProgressEvent) -> None:
        self._latest[task_id] = event
        for queue in tuple(self._subscribers.get(task_id, set())):
            queue.put_nowait(event)
        for queue in tuple(self._global_subscribers):
            queue.put_nowait(event)

    def latest(self, task_id: int) -> DownloadProgressEvent | None:
        return self._latest.get(task_id)

    async def subscribe(self, task_id: int) -> AsyncIterator[DownloadProgressEvent]:
        queue: asyncio.Queue[DownloadProgressEvent] = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            latest = self.latest(task_id)
            if latest is not None:
                yield latest
            while True:
                yield await queue.get()
        finally:
            subscribers = self._subscribers.get(task_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    async def subscribe_all(self) -> AsyncIterator[DownloadProgressEvent]:
        queue: asyncio.Queue[DownloadProgressEvent] = asyncio.Queue()
        self._global_subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._global_subscribers.discard(queue)


_progress_broker = TaskProgressBroker()


def get_progress_broker() -> TaskProgressBroker:
    return _progress_broker
