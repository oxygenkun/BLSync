"""Move task control and worker ownership into the database."""

VERSION = 2

STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN control_action VARCHAR(10) NOT NULL DEFAULT 'run'",
    "ALTER TABLE tasks ADD COLUMN worker_id VARCHAR(100)",
    "ALTER TABLE tasks ADD COLUMN lease_expires_at DATETIME",
    "UPDATE tasks SET control_action = 'pause' WHERE status = 'paused'",
    "UPDATE tasks SET status = 'ready' WHERE status IN ('consuming', 'downloading')",
    "CREATE INDEX ix_tasks_lease_expires_at ON tasks (lease_expires_at)",
)
