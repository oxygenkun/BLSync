"""
任务消费者基础模块
"""
import dataclasses
from abc import ABC, abstractmethod

from ..configs import Config


@dataclasses.dataclass
class TaskContext(ABC):
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
