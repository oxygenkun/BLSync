"""Validated domain models for BLSync configuration."""

import pathlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EDITABLE_FIELDS = frozenset(
    {
        "interval",
        "request_timeout",
        "max_concurrent_tasks",
        "task_timeout",
        "download_retry_limit",
        "download_stall_timeout",
        "download_url_refresh_retries",
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


class FavoriteListConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    fid: str
    path: str
    name: str | None = None
    name_group: str | None = None
    postprocess: list[PostprocessConfigT] | None = None

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
    """

    model_config = ConfigDict(frozen=True)

    config_file: pathlib.Path
    data_path: pathlib.Path
    verbose: bool
    log_level: str
    interval: int = Field(ge=1)
    request_timeout: int = Field(ge=1)
    max_concurrent_tasks: int = Field(ge=1, le=64)
    task_timeout: int = Field(ge=1)
    download_retry_limit: int = Field(ge=0, le=100)
    download_stall_timeout: float = Field(gt=0)
    download_url_refresh_retries: int = Field(ge=0, le=20)
    retry_failed_tasks: bool
    credential: ConfigCredential
    favorite_list: dict[str, FavoriteListConfig]

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
