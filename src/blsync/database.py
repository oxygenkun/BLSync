"""
Shared database utilities and initialization.
"""

import asyncio

from blsync import get_global_configs
from blsync.model.task import BiliVideoTaskDAL
from blsync.model.video import VideoDAL

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


def get_video_dal() -> VideoDAL:
    """Get a video database access layer bound to the current task DAL."""
    # VideoDAL 是无状态的轻量封装，每次基于当前 task DAL 的会话工厂创建，
    # 避免缓存绑定到已失效的数据库连接（尤其是测试替换 _task_dal 的场景）
    return VideoDAL(get_task_dal().async_session)


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_global_configs().max_concurrent_tasks)
    return _semaphore
