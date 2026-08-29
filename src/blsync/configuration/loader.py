"""Command-line parsing and TOML <-> Config mapping for configuration models.

``Config`` (pydantic) is the single source of truth: the TOML file is a
serialized projection produced by :func:`dump_config`, and parsed back into a
``Config`` by :func:`build_config` (initial load and external reloads).
"""

import argparse
import pathlib
from collections.abc import Sequence

import toml
import tomli_w

from .models import Config


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


def build_config(
    config_file: pathlib.Path,
    *,
    strict_favorites: bool = False,
) -> Config:
    """Build an effective, validated configuration from a TOML file.

    Field defaults, ``data_path`` normalization, and favorite-list parsing
    (strict vs. skip-invalid) all live on the model itself.
    """
    config = Config.model_validate(
        {**toml.load(config_file), "config_file": config_file},
        context={"strict_favorites": strict_favorites},
    )
    config.data_path.parent.mkdir(parents=True, exist_ok=True)
    return config


def dump_config(config: Config) -> str:
    """Serialize the active ``Config`` into its TOML projection.

    The document is generated from the model (field serializers project
    ``data_path``, credentials, and favorite lists into TOML shape), so the
    persisted file always mirrors the in-memory state. Postprocess lists are
    written as inline arrays inside their favorite-list table (via tomli-w)
    instead of ``[[...]]`` array-of-tables blocks.
    """
    return tomli_w.dumps(config.model_dump(exclude={"config_file"}))
