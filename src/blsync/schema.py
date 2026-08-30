"""SQLite schema version management and startup migrations."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from blsync.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS


class SchemaMigrationError(RuntimeError):
    """The database schema cannot be safely migrated by this application."""


async def migrate_database(engine: AsyncEngine) -> int:
    """Apply all pending migrations under a SQLite write lock."""
    versions = [migration.VERSION for migration in MIGRATIONS]
    expected = list(range(1, LATEST_SCHEMA_VERSION + 1))
    if versions != expected:
        raise SchemaMigrationError(
            f"Migration versions must be continuous: expected {expected}, got {versions}"
        )

    async with engine.connect() as conn:
        await conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            tables = {
                row[0]
                for row in (
                    await conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ).all()
            }
            if "schema_version" not in tables:
                await conn.exec_driver_sql(
                    """
                    CREATE TABLE schema_version (
                        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
                        version INTEGER NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
                baseline = 1 if "tasks" in tables else 0
                await conn.exec_driver_sql(
                    "INSERT INTO schema_version (id, version, updated_at) VALUES (1, ?, ?)",
                    (baseline, datetime.now(UTC).isoformat()),
                )

            result = await conn.exec_driver_sql(
                "SELECT version FROM schema_version WHERE id = 1"
            )
            current = result.scalar_one_or_none()
            if current is None:
                raise SchemaMigrationError(
                    "schema_version must contain the singleton row"
                )
            if current > LATEST_SCHEMA_VERSION:
                raise SchemaMigrationError(
                    f"Database schema version {current} is newer than supported "
                    f"version {LATEST_SCHEMA_VERSION}"
                )

            for migration in MIGRATIONS[current:]:
                for statement in migration.STATEMENTS:
                    await conn.exec_driver_sql(statement)
                await conn.exec_driver_sql(
                    "UPDATE schema_version SET version = ?, updated_at = ? WHERE id = 1",
                    (migration.VERSION, datetime.now(UTC).isoformat()),
                )
                current = migration.VERSION

            await conn.commit()
            return current
        except BaseException:
            await conn.rollback()
            raise
