# Database migrations

The application applies the migrations in this package automatically before the
web server starts accepting work. Back up `data.sqlite3` before upgrading.

To add a migration, create the next continuous `vNNN_name.py` module, set its
`VERSION`, expose its SQL through `STATEMENTS`, and append it to `MIGRATIONS` in
`__init__.py`. Every statement and the `schema_version` update run in one
transaction. Published migrations are immutable: fixes must be a new version.

Only forward migrations are supported. Missing/duplicate versions and databases
newer than the running application cause startup to fail.

