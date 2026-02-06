# Consumer 模块架构说明

## 概述

这个 consumer 模块实现了基于多态分派的任务处理系统，按功能模块化。系统采用生产者-消费者模式，支持并发任务处理和任务去重。

## 模块结构

```
consumer/
├── __init__.py          # 包初始化
├── base.py             # 基础抽象类定义
├── bilibili.py         # Bilibili视频下载任务处理
└── README.md           # 本说明文档
```

## 核心组件

### 1. 基础抽象类 (`base.py`)

- **`TaskContext`**: 任务上下文基类，包含任务特定的参数
- **`Task`**: 任务执行基类，定义任务执行接口
- **`Postprocess`**: 后处理基类，定义任务完成后的处理逻辑

### 2. Bilibili 任务实现 (`bilibili.py`)

- **`BiliVideoTaskContext`**: Bilibili 视频任务上下文
- **`BiliVideoTask`**: Bilibili 视频下载任务实现
- **`BiliVideoPostprocessMove`**: 移动视频到其他收藏夹的后处理
- **`BiliVideoPostprocessRemove`**: 从收藏夹移除视频的后处理

## 设计模式

### 1. Strategy Pattern (策略模式)

- `Task` 作为抽象策略
- 各种具体任务类型作为具体策略
- `task_consumer` 作为上下文执行器

### 2. Command Pattern (命令模式)

- 每个任务实例封装一个完整的请求
- 队列作为命令缓冲区
- 消费者执行这些命令

### 3. Polymorphic Dispatch (多态分派)

- 通过 `task.execute()` 避免类型检查
- 符合开闭原则，易于扩展

## 任务处理流程

### 1. 任务创建

```python
# 在 main.py 中创建任务上下文
# 注意: config 现在是全局变量，通过 get_global_configs() 获取
context = BiliVideoTaskContext(bid=bvid, task_name=task_name)
```

### 2. 任务分发

```python
# 任务消费者根据上下文类型创建对应的任务实例
match task_context:
    case BiliVideoTaskContext():
        task = BiliVideoTask(task_context)
```

### 3. 任务执行

```python
# 任务执行包含下载和后处理两个阶段
await task.execute()  # 执行下载
await task.execute_postprocess()  # 执行后处理
```

## 并发控制

- **信号量控制**: 使用 `asyncio.Semaphore` 限制最大并发任务数
- **任务去重**: 通过 `queued_tasks` 和 `processing_tasks` 集合避免重复任务
- **超时控制**: 每个任务都有超时限制，防止任务卡死
- **定期清理**: 定期清理已下载但仍在追踪集合中的任务

## 后处理机制

支持多种后处理操作：

1. **移动操作** (`MovePostprocessConfig`): 将视频移动到其他收藏夹
2. **移除操作** (`RemovePostprocessConfig`): 从收藏夹中移除视频

## 使用方法

### 1. 创建新的任务类型

```python
class MyCustomTaskContext(TaskContext):
    param1: str
    param2: int

class MyCustomTask(Task):
    def __init__(self, task_context: MyCustomTaskContext):
        self._task_context = task_context

    def get_task_key(self) -> tuple:
        return ("mycustom", self._task_context.param1, self._task_context.param2)

    async def execute(self) -> None:
        # 实现具体的任务逻辑
        pass
```

### 2. 在主程序中使用

```python
# 在 main.py 的 task_consumer 中添加新的任务类型处理
match task_context:
    case BiliVideoTaskContext():
        task = BiliVideoTask(task_context)
    case MyCustomTaskContext():
        task = MyCustomTask(task_context)
    case _:
        logger.warning(f"Unknown task context: {task_context}")
        task = None
```

## 优势

1. **模块化**: 每种任务类型有自己的文件
2. **可扩展**: 添加新任务类型只需创建新文件
3. **类型安全**: 每个任务知道如何执行自己
4. **易于测试**: 每个任务类型可以独立测试
5. **职责分离**: 消费者不需要知道具体实现
6. **并发控制**: 支持任务并发和去重
7. **后处理**: 支持任务完成后的自定义处理

## 配置说明

任务配置通过 `config.toml` 文件进行管理：

- **下载路径**: 支持时间格式化变量 (`{YYYY}`, `{MM}`, `{DD}` 等)
- **文件名模板**: 支持自定义文件名格式
- **后处理配置**: 支持移动和移除操作
- **并发控制**: 可配置最大并发任务数和超时时间
