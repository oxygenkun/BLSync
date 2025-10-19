# Consumer 模块架构说明

## 概述

这个 consumer 模块实现了基于多态分派的任务处理系统，将原来集中在 `main.py` 和 `downloader.py` 中的代码按功能模块化。

## 模块结构

```
consumer/
├── __init__.py          # 包初始化
├── base.py             # 基础抽象类定义
├── bilibili.py         # Bilibili视频下载任务处理
├── audio.py            # 音频下载任务处理（示例）
└── playlist.py         # 播放列表处理任务（示例）
```

## 设计模式

### 1. Strategy Pattern (策略模式)
- `TaskContext` 作为抽象策略
- 各种具体任务类型作为具体策略
- `task_consumer` 作为上下文执行器

### 2. Command Pattern (命令模式)  
- 每个任务实例封装一个完整的请求
- 队列作为命令缓冲区
- 消费者执行这些命令

### 3. Polymorphic Dispatch (多态分派)
- 通过 `task.execute()` 避免类型检查
- 符合开闭原则，易于扩展

## 使用方法

### 1. 创建新的任务类型

```python
class MyCustomTaskContext(TaskContext):
    param1: str
    param2: int
    
    def get_task_key(self) -> tuple:
        return ("mycustom", self.param1, self.param2)
    
    async def execute(self) -> None:
        # 实现具体的任务逻辑
        pass
```

### 2. 在主程序中使用

```python
from .consumer.bilibili import BiliVideoTaskContext
from .consumer.mycustom import MyCustomTaskContext

# 创建任务
task = BiliVideoTaskContext(config=config, bid="BV123", favid="456")
await task_queue.put(task)
```

## 优势

1. **模块化**: 每种任务类型有自己的文件
2. **可扩展**: 添加新任务类型只需创建新文件
3. **类型安全**: 每个任务知道如何执行自己
4. **易于测试**: 每个任务类型可以独立测试
5. **职责分离**: 消费者不需要知道具体实现

## 迁移说明

- 原 `downloader.py` 中的函数迁移到 `consumer/bilibili.py`
- 原 `main.py` 中的 `BiliVideoTaskContext` 迁移到 `consumer/bilibili.py`
- 任务队列处理逻辑保持在 `main.py` 中，但使用多态分派
