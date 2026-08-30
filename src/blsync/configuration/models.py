"""Validated domain models for BLSync configuration."""

import pathlib
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_serializer,
    field_validator,
)

EDITABLE_FIELDS = frozenset(
    {
        "interval",
        "request_timeout",
        "max_concurrent_tasks",
        "task_timeout",
        "retry_failed_tasks",
        "log_level",
        "credential",
        "favorite_list",
    }
)


class ConfigCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    sessdata: str | None = None
    bili_jct: str | None = None
    buvid3: str | None = None
    dedeuserid: str | None = None
    ac_time_value: str | None = None

    def __hash__(self) -> int:
        return hash(tuple(self.model_dump().values()))


class MovePostprocessConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Literal["move"] = "move"
    fid: str

    @field_validator("fid", mode="before")
    @classmethod
    def coerce_fid(cls, value: object) -> object:
        """TOML authors write numeric fids; coerce them before validation."""
        if isinstance(value, int | float):
            return str(value)
        return value


class RemovePostprocessConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["remove"] = "remove"


class SavePostprocessConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Literal["save"] = "save"
    fid: str

    @field_validator("fid", mode="before")
    @classmethod
    def coerce_fid(cls, value: object) -> object:
        """TOML authors write numeric fids; coerce them before validation."""
        if isinstance(value, int | float):
            return str(value)
        return value


type PostprocessConfigT = Annotated[
    MovePostprocessConfig | RemovePostprocessConfig | SavePostprocessConfig,
    Field(discriminator="action"),
]


DEFAULT_NAME_TEMPLATE = "[{username}]{name}({bvid})"
DEFAULT_NAME_GROUP_TEMPLATE = "[{username}]{title}({bvid})/P{id:0>3}-{name}"


class FavoriteListConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    fid: str
    path: str
    name: str = DEFAULT_NAME_TEMPLATE
    name_group: str = DEFAULT_NAME_GROUP_TEMPLATE
    postprocess: list[PostprocessConfigT] | None = None

    @field_validator("name", "name_group", mode="before")
    @classmethod
    def use_default_name_template(cls, value: object, info: ValidationInfo) -> object:
        if value not in (None, ""):
            return value
        return (
            DEFAULT_NAME_GROUP_TEMPLATE
            if info.field_name == "name_group"
            else DEFAULT_NAME_TEMPLATE
        )

    @field_validator("fid", mode="before")
    @classmethod
    def coerce_fid(cls, value: object) -> object:
        if isinstance(value, int | float):
            return str(value)
        return value

    @field_validator("fid")
    @classmethod
    def validate_fid(cls, value: str) -> str:
        try:
            int(value)
        except ValueError as exc:
            raise ValueError("fid must be an integer string") from exc
        return value


class Config(BaseModel):
    """Effective process configuration after file and CLI values are combined.

    Immutable: every mutation must go through ``ConfigStore.update`` so the
    store can validate, persist, and notify change observers.

    The model is the single source of truth for the TOML document: field
    defaults mirror the loader's fallbacks, before-validators normalize
    TOML-shaped input (directory-style ``data_path``, favorite-list string
    shorthand with skip-invalid semantics), and field serializers project the
    model back into TOML shape for persistence.
    """

    model_config = ConfigDict(frozen=True)

    config_file: pathlib.Path
    data_path: pathlib.Path
    verbose: bool = False
    log_level: str = "INFO"
    interval: int = Field(ge=1)
    request_timeout: int = Field(ge=1)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=64)
    task_timeout: int = Field(default=300, ge=1)
    retry_failed_tasks: bool = False
    credential: ConfigCredential
    favorite_list: dict[str, FavoriteListConfig] = Field(
        default_factory=lambda: {
            "-1": FavoriteListConfig(fid="-1", path="sync/"),
        }
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {
            "TRACE",
            "DEBUG",
            "INFO",
            "SUCCESS",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {', '.join(sorted(allowed))}")
        return normalized

    @field_validator("data_path", mode="before")
    @classmethod
    def _data_path_to_sqlite(cls, value: object) -> pathlib.Path:
        """Accept either the TOML directory (``config/``) or a full sqlite path."""
        if isinstance(value, pathlib.Path):
            if value.name == "data.sqlite3":
                return value
            return value / "data.sqlite3"
        text = str(value)
        if text.endswith("data.sqlite3"):
            return pathlib.Path(text)
        return pathlib.Path(text) / "data.sqlite3"

    @field_validator("favorite_list", mode="before")
    @classmethod
    def _parse_favorite_lists(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> dict[str, FavoriteListConfig]:
        """Parse TOML favorite entries, seeding the default direct-download task.

        Invalid items are skipped with a warning on reads; pass
        ``context={"strict_favorites": True}`` (the update path) to raise
        instead of silently dropping them.
        """
        strict = bool((info.context or {}).get("strict_favorites"))
        favorites: dict[str, FavoriteListConfig] = {
            "-1": FavoriteListConfig(fid="-1", path="sync/"),
        }
        items = value.items() if isinstance(value, Mapping) else {}
        for key, item in items:
            try:
                if isinstance(item, str):
                    favorites[key] = FavoriteListConfig(fid=key, path=item)
                elif isinstance(item, Mapping):
                    favorites[key] = FavoriteListConfig.model_validate(item)
                else:
                    raise TypeError(f"unsupported value type: {type(item).__name__}")
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                if strict:
                    raise ValueError(
                        f"Invalid favorite_list item {key!r}: {exc}"
                    ) from exc
                logger.warning(
                    f"Skip invalid favorite_list item {key!r}: {exc}; value={item!r}"
                )
        return favorites

    @field_serializer("data_path")
    def _dump_data_path(self, value: pathlib.Path) -> str:
        """Project the sqlite path back to its TOML directory form."""
        return str(value.parent)

    @field_serializer("credential")
    def _dump_credential(self, value: ConfigCredential) -> dict[str, str]:
        """TOML keeps every secret key; unset secrets are written as empty strings."""
        return {
            name: (secret if secret is not None else "")
            for name, secret in value.model_dump().items()
        }

    @field_serializer("favorite_list")
    def _dump_favorite_list(
        self,
        value: dict[str, FavoriteListConfig],
    ) -> dict[str, dict[str, Any]]:
        """Project favorites into TOML shape: integer fids, omitted nulls."""
        dumped: dict[str, dict[str, Any]] = {}
        for task_name, favorite in value.items():
            item: dict[str, Any] = {"fid": int(favorite.fid), "path": favorite.path}
            if favorite.name is not None:
                item["name"] = favorite.name
            if favorite.name_group is not None:
                item["name_group"] = favorite.name_group
            if favorite.postprocess:
                item["postprocess"] = []
                for action in favorite.postprocess:
                    rendered = action.model_dump()
                    if "fid" in rendered:
                        rendered["fid"] = int(rendered["fid"])
                    item["postprocess"].append(rendered)
            dumped[task_name] = item
        return dumped


def apply_config_changes(
    current: Config,
    changes: Mapping[str, Any],
) -> Config:
    """Build a new validated Config by applying field changes to ``current``.

    ``Config`` is the single source of truth: credential secrets merge (``None``
    keeps the existing value, an empty string clears it), while every other
    editable field is a whole-value replacement so deletions persist. The
    result is fully re-validated through the model, so an invalid update can
    never be persisted.
    """
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"Unsupported configuration fields: {fields}")
    raw: dict[str, Any] = current.model_dump()
    for key, value in changes.items():
        if key == "credential" and isinstance(value, Mapping):
            credential = dict(raw["credential"])
            for secret_name, secret_value in value.items():
                # None retains an existing secret; an empty string clears it.
                if secret_value is not None:
                    credential[secret_name] = secret_value
            raw["credential"] = credential
        else:
            raw[key] = value
    return Config.model_validate(raw, context={"strict_favorites": True})
