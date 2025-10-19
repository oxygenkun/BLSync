"""
任务消费者基础模块
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from ..configs import Config


class TaskContext(BaseModel, ABC):
    """任务上下文基类"""

    config: Config

    @abstractmethod
    async def execute(self) -> None:
        """Execute the task"""
        pass

    @abstractmethod
    def get_task_key(self) -> tuple:
        """Get unique identifier for the task"""
        pass
