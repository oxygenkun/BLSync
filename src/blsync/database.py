"""
Shared database utilities and initialization.
"""
import asyncio

from blsync import get_global_configs
from blsync.task_models import BiliVideoTaskDAL

# Global task database access layer
_task_dal: BiliVideoTaskDAL | None = None

# 创建信号量来控制并发任务数
_semaphore = None


def get_task_dal() -> BiliVideoTaskDAL:
    """Get the global task database access layer."""
    global _task_dal
    if _task_dal is None:
        config = get_global_configs()
        db_path = config.data_path
        # Convert pathlib Path to string and ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_path}"
        _task_dal = BiliVideoTaskDAL(db_url)
    return _task_dal


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_global_configs().max_concurrent_tasks)
    return _semaphore
