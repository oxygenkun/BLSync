"""Built-in, forward-only database migrations."""

from blsync.migrations import v001_initial, v002_task_control

MIGRATIONS = (v001_initial, v002_task_control)
LATEST_SCHEMA_VERSION = len(MIGRATIONS)

