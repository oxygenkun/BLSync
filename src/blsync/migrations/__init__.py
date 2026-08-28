"""Built-in, forward-only database migrations."""

from blsync.migrations import v001_initial, v002_task_control, v003_video_metadata

MIGRATIONS = (v001_initial, v002_task_control, v003_video_metadata)
LATEST_SCHEMA_VERSION = len(MIGRATIONS)

