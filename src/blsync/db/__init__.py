"""
Shared database utilities and initialization.
"""

from blsync.configuration.store import get_config

from .task import BiliVideoTaskDAL
from .video import VideoDAL

# Global task database access layer
_task_dal: BiliVideoTaskDAL | None = None


def get_task_dal() -> BiliVideoTaskDAL:
    """Get the global task database access layer."""
    global _task_dal
    if _task_dal is None:
        config = get_config()
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
