"""Command-line and TOML loading for configuration domain models."""

import argparse
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

import toml
from loguru import logger
from pydantic import ValidationError

from .models import (
    Config,
    ConfigCredential,
    FavoriteListConfig,
)


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="blsync: bili-sync")
    parser.add_argument(
        "-c",
        "--config",
        type=pathlib.Path,
        default="./config/config.toml",
        help="Path to the configuration file",
    )
    return parser.parse_known_args(args)[0]


def _parse_favorite_lists(
    raw: Mapping[str, Any],
    *,
    strict: bool,
) -> dict[str, FavoriteListConfig]:
    favorites = {"-1": FavoriteListConfig(fid="-1", path="sync/")}
    for key, value in raw.get("favorite_list", {}).items():
        try:
            if isinstance(value, str):
                favorites[key] = FavoriteListConfig(fid=key, path=value)
            elif isinstance(value, dict):
                # Raw TOML values flow straight into pydantic: fid ints are
                # coerced by the model, and postprocess items are dispatched
                # automatically via the "action" discriminator.
                favorites[key] = FavoriteListConfig.model_validate(value)
            else:
                raise TypeError(f"unsupported value type: {type(value).__name__}")
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            if strict:
                raise ValueError(f"Invalid favorite_list item {key!r}: {exc}") from exc
            logger.warning(
                f"Skip invalid favorite_list item {key!r}: {exc}; value={value!r}"
            )
    return favorites


def build_config(
    config_file: pathlib.Path,
    *,
    strict_favorites: bool = False,
) -> Config:
    """Build an effective, validated configuration from a TOML file."""
    raw = toml.load(config_file)
    config = Config(
        config_file=config_file,
        data_path=pathlib.Path(
            raw.get("data_path", "./"),
            "data.sqlite3",
        ),
        verbose=bool(raw.get("verbose", False)),
        log_level=raw.get("log_level", "INFO"),
        interval=raw["interval"],
        request_timeout=raw["request_timeout"],
        max_concurrent_tasks=raw.get("max_concurrent_tasks", 3),
        task_timeout=raw.get("task_timeout", 300),
        download_retry_limit=raw.get("download_retry_limit", 10),
        download_stall_timeout=raw.get("download_stall_timeout", 120.0),
        download_url_refresh_retries=raw.get("download_url_refresh_retries", 2),
        retry_failed_tasks=raw.get("retry_failed_tasks", False),
        credential=ConfigCredential(
            sessdata=raw["credential"]["sessdata"],
            bili_jct=raw["credential"]["bili_jct"],
            buvid3=raw["credential"].get("buvid3") or None,
            dedeuserid=raw["credential"].get("dedeuserid") or None,
            ac_time_value=raw["credential"].get("ac_time_value") or None,
        ),
        favorite_list=_parse_favorite_lists(raw, strict=strict_favorites),
    )
    config.data_path.parent.mkdir(parents=True, exist_ok=True)
    return config
