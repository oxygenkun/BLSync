"""Configuration store: hot reload, validation, atomic persistence, and change observers.

The store is the single owner of the active configuration:

    config.toml <--> ConfigStore <--> application code

- Program -> file: :meth:`ConfigStore.update` applies changes on the ``Config``
  model, validates it, serializes it into its TOML projection, writes it
  atomically, then publishes.
- File -> program: :meth:`ConfigStore.get` polls the file signature and
  reloads valid external changes; reload never writes the file, which keeps
  the two-way binding loop-free.
- Both directions notify field-level change observers registered through
  :meth:`ConfigStore.on_change`.

The ``Config`` pydantic model is the single source of truth: the TOML file
is only a serialized projection of the active ``Config``, and every internal
read goes through the model.
"""

import asyncio
import hashlib
import os
import pathlib
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from loguru import logger

from .loader import build_config, dump_config, parse_args
from .models import EDITABLE_FIELDS, Config, apply_config_changes

type FileSignature = tuple[int, int]
type ChangeCallback = Callable[[Any, Any], None]


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Effective configuration paired with its persistence revision."""

    config: Config
    revision: str


class ConfigurationError(Exception):
    """Base class for expected configuration update failures."""


class ConfigRevisionConflict(ConfigurationError):
    pass


class ConfigUpdateInvalid(ConfigurationError):
    pass


def _sync_log_level(old: str, new: str) -> None:
    """Apply a changed log level to the process-wide loguru sink."""
    logger.remove()
    logger.add(sys.stderr, level=new)


class ConfigStore:
    """Own the active configuration and its persisted TOML document.

    The exposed :class:`Config` objects are immutable; every mutation goes
    through :meth:`update` so the store can validate, persist, and notify.
    """

    def __init__(self) -> None:
        config_file = parse_args().config
        self._current: Config = build_config(config_file)
        self._signature: FileSignature = self._file_signature(config_file)
        self._runtime_lock = threading.RLock()
        self._update_lock = asyncio.Lock()
        self._observers: dict[str, list[ChangeCallback]] = {}
        self.on_change("log_level", _sync_log_level)

    def get(self) -> Config:
        """Return the active configuration, reloading valid external changes."""
        previous: Config | None = None
        with self._runtime_lock:
            signature = self._file_signature(self._current.config_file)
            if signature != self._signature:
                previous = self._reload_external_change(signature)
            current = self._current
        if previous is not None:
            self._notify(previous, current)
        return current

    def get_snapshot(self) -> ConfigurationSnapshot:
        """Return the active configuration and its current file revision."""
        config = self.get()
        return ConfigurationSnapshot(
            config=config,
            revision=self._revision(config.config_file),
        )

    def get_value(self, field: str) -> Any:
        """Read a single top-level configuration field."""
        if field not in Config.model_fields:
            raise ValueError(f"Unknown configuration field: {field!r}")
        return getattr(self.get(), field)

    def on_change(
        self,
        field: str,
        callback: ChangeCallback | None = None,
    ) -> ChangeCallback | Callable[[ChangeCallback], ChangeCallback]:
        """Subscribe to changes of a top-level configuration field.

        Usable as a plain call or as a decorator::

            @store.on_change("log_level")
            def sync_logging(old: str, new: str) -> None: ...

        Callbacks fire for both API-driven updates and external file
        reloads. They run synchronously, outside the store locks, and must
        be fast and non-blocking; exceptions are logged and isolated so one
        failing observer cannot break the others.

        TODO(config-observer): migrate polling consumers (scheduler
        concurrency, producer interval, task timeout in main.py) to this
        API.
        """
        if field not in Config.model_fields:
            raise ValueError(f"Unknown configuration field: {field!r}")
        if callback is None:
            return lambda fn: self.on_change(field, fn)
        with self._runtime_lock:
            self._observers.setdefault(field, []).append(callback)
        return callback

    async def update(
        self,
        revision: str,
        changes: Mapping[str, Any],
    ) -> ConfigurationSnapshot:
        async with self._update_lock:
            current = self.get()
            self._check_update(current, revision, changes)
            updated = self._build_updated_config(current, changes)
            with self._runtime_lock:
                previous = self._current
                self._current = updated
                self._signature = self._file_signature(updated.config_file)
            logger.info(f"Configuration updated: {', '.join(sorted(changes))}")
            self._notify(previous, updated)
            return ConfigurationSnapshot(
                config=updated,
                revision=self._revision(updated.config_file),
            )

    def _check_update(
        self,
        current: Config,
        revision: str,
        changes: Mapping[str, Any],
    ) -> None:
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ConfigUpdateInvalid(f"Unsupported configuration fields: {fields}")
        if self._revision(current.config_file) != revision:
            raise ConfigRevisionConflict(
                "Configuration file changed since it was loaded"
            )

    def _build_updated_config(
        self,
        current: Config,
        changes: Mapping[str, Any],
    ) -> Config:
        """Validate an update on the Config model and persist it atomically.

        The new ``Config`` is computed from ``current`` plus ``changes`` and
        fully re-validated; only then is its TOML projection written. The
        returned model is exactly what lands on disk, keeping the store's
        in-memory state aligned with the file.
        """
        try:
            updated = apply_config_changes(current, changes)
            rendered = dump_config(updated)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigUpdateInvalid(str(exc)) from exc
        self._atomic_replace(current.config_file, rendered)
        return updated

    def _reload_external_change(self, signature: FileSignature) -> Config | None:
        """Apply a valid external file change and return the previous config.

        Returns None when the new document is invalid and gets ignored.
        This path never writes the file: program changes flow memory ->
        file, external changes flow file -> memory.
        """
        previous = self._current
        try:
            reloaded = build_config(previous.config_file)
            # Database connections are process-lifetime resources.
            reloaded = reloaded.model_copy(update={"data_path": previous.data_path})
            self._current = reloaded
            self._signature = signature
            logger.info("Reloaded configuration after an external file change")
            return previous
        except (OSError, KeyError, TypeError, ValueError) as exc:
            self._signature = signature
            logger.error(f"Ignoring invalid configuration file update: {exc}")
            return None

    def _notify(self, previous: Config, current: Config) -> None:
        """Invoke observers for fields whose values actually changed."""
        with self._runtime_lock:
            observers = {field: tuple(cbs) for field, cbs in self._observers.items()}
        if not observers:
            return
        old_values = previous.model_dump()
        new_values = current.model_dump()
        for field, callbacks in observers.items():
            old = old_values.get(field)
            new = new_values.get(field)
            if old == new:
                continue
            for callback in callbacks:
                try:
                    callback(old, new)
                except Exception:
                    logger.exception(
                        f"Config change observer failed for field {field!r}"
                    )

    @staticmethod
    def _atomic_replace(config_file: pathlib.Path, rendered: str) -> None:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        file_mode = config_file.stat().st_mode
        temporary_path: pathlib.Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=config_file.parent,
                prefix=f".{config_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = pathlib.Path(temporary.name)
            temporary_path.chmod(file_mode)
            os.replace(temporary_path, config_file)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _revision(config_file: pathlib.Path) -> str:
        return hashlib.sha256(config_file.read_bytes()).hexdigest()

    @staticmethod
    def _file_signature(config_file: pathlib.Path) -> FileSignature:
        stat = config_file.stat()
        return stat.st_mtime_ns, stat.st_size


config_store = ConfigStore()


def get_config() -> Config:
    return config_store.get()
