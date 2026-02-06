"""
任务消费者基础模块
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class TaskContext(BaseModel, ABC):
    """任务上下文基类"""

    # @abstractmethod
    # async def execute(self) -> None:
    #     """Execute the task"""
    #     pass

    # @abstractmethod
    # def get_task_key(self) -> tuple:
    #     """Get unique identifier for the task"""
    #     pass


class Task(ABC):
    """任务基类"""

    # _task_context: TaskContext

    # def __init__(self, task_context: TaskContext):
    #     self._task_context = task_context

    @abstractmethod
    async def execute(self) -> None:
        """Execute the task"""
        pass

    @abstractmethod
    def get_task_key(self) -> tuple:
        """Get unique identifier for the task"""


class Postprocess(BaseModel, ABC):
    """后处理基类"""

    # _task_context: TaskContext

    # def __init__(self, task_context: TaskContext):
    #     self._task_context = task_context

    @abstractmethod
    async def execute(self) -> None:
        """Execute the postprocess"""
        pass
