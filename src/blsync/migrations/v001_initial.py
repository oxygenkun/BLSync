"""Create the original tasks schema."""

VERSION = 1

STATEMENTS = (
    """
    CREATE TABLE tasks (
        id INTEGER NOT NULL PRIMARY KEY,
        task_type VARCHAR(50) NOT NULL,
        task_key VARCHAR(500) NOT NULL,
        task_data TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        completed_at DATETIME,
        error_message TEXT
    )
    """,
    "CREATE UNIQUE INDEX ix_tasks_task_key ON tasks (task_key)",
    "CREATE INDEX ix_tasks_task_type ON tasks (task_type)",
    "CREATE INDEX ix_tasks_status ON tasks (status)",
    "CREATE INDEX ix_tasks_created_at ON tasks (created_at)",
)
