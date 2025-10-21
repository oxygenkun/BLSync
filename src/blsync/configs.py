import abc
import argparse
import pathlib

import toml
from pydantic import BaseModel, ConfigDict


def parse_command_line_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="blsync: bili-sync")
    parser.add_argument(
        "-c",
        "--config",
        type=pathlib.Path,
        default="./config/config.toml",
        help="Path to the configuration file",
    )

    parser.add_argument(
        "-d",
        "--data",
        type=pathlib.Path,
        # default="./data.sqlite3",
        help="Path to the data file",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show verbose output",
    )

    parser.add_argument(
        "--max-concurrent-tasks",
        type=int,
        help="Maximum number of concurrent download tasks",
    )

    parser.add_argument(
        "--task-timeout",
        type=int,
        help="Task timeout in seconds",
    )

    return parser.parse_args(args)


class ConfigCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    sessdata: str | None = None
    bili_jct: str | None = None
    buvid3: str | None = None
    dedeuserid: str | None = None
    ac_time_value: str | None = None


class MovePostprocessConfig(BaseModel):
    action: str = "move"
    fid: str


class RemovePostprocessConfig(BaseModel):
    action: str = "remove"


type PostprocessConfigT = MovePostprocessConfig | RemovePostprocessConfig


class FavoriteListConfig(BaseModel):
    fid: str
    path: str
    name: str | None = None
    postprocess: list[PostprocessConfigT] | None = None


class Config(BaseModel):
    config_file: pathlib.Path
    data_path: pathlib.Path

    verbose: bool

    interval: int
    request_timeout: int
    max_concurrent_tasks: int
    task_timeout: int
    credential: ConfigCredential
    favorite_list: dict[str, FavoriteListConfig]


def _post_process_match(value: dict) -> MovePostprocessConfig | RemovePostprocessConfig:
    match value["action"]:
        case "move":
            return MovePostprocessConfig(fid=str(value["fid"]))
        case "remove":
            return RemovePostprocessConfig()


def load_configs(args=None) -> Config:
    """
    Helper function to load all configurations
    from the configuration file and command line.
    """
    # TODO: 命令行参数覆盖配置文件
    args = parse_command_line_args(args)
    toml_config = toml.load(args.config)

    # 处理favorite_list配置，支持复杂配置格式
    # favorite_list = {}
    favorite_list: dict[str, FavoriteListConfig] = {
        "-1": FavoriteListConfig(fid="-1", path="sync/"),
    }
    if "favorite_list" in toml_config:
        for key, value in toml_config["favorite_list"].items():
            if isinstance(value, str):
                # 简单格式: fid = "path"
                favorite_list[key] = FavoriteListConfig(fid=key, path=value)
            elif isinstance(value, dict):
                # 复杂格式: [favorite_list.taskname] with fid, path, name, postprocess
                favorite_list[key] = FavoriteListConfig(
                    fid=str(value["fid"]),
                    path=value["path"],
                    name=value.get("name"),
                    postprocess=[_post_process_match(p) for p in value["postprocess"]],
                )
            else:
                raise ValueError(f"Invalid favorite_list configuration: {value}")

    config = Config(
        config_file=args.config,
        data_path=pathlib.Path(
            (
                args.data
                if args.data
                else pathlib.Path(toml_config.get("data_path", "./"))
            ),
            "data.sqlite3",
        ),
        verbose=args.verbose,
        interval=toml_config["interval"],
        request_timeout=toml_config["request_timeout"],
        max_concurrent_tasks=(
            args.max_concurrent_tasks
            if args.max_concurrent_tasks is not None
            else toml_config.get("max_concurrent_tasks", 3)
        ),
        task_timeout=(
            args.task_timeout
            if args.task_timeout is not None
            else toml_config.get("task_timeout", 300)
        ),
        credential=ConfigCredential(
            sessdata=toml_config["credential"]["sessdata"],
            bili_jct=toml_config["credential"]["bili_jct"],
            buvid3=(
                toml_config["credential"]["buvid3"]
                if "buvid3" in toml_config["credential"]
                and toml_config["credential"]["buvid3"] != ""
                else None
            ),
            dedeuserid=(
                toml_config["credential"]["dedeuserid"]
                if "dedeuserid" in toml_config["credential"]
                and toml_config["credential"]["dedeuserid"] != ""
                else None
            ),
            ac_time_value=(
                toml_config["credential"]["ac_time_value"]
                if "ac_time_value" in toml_config["credential"]
                and toml_config["credential"]["ac_time_value"] != ""
                else None
            ),
        ),
        favorite_list=favorite_list,
    )

    if not config.data_path.parent.exists():
        config.data_path.parent.mkdir(parents=True)

    return config


def save_cookies_to_txt(credential: ConfigCredential, path: pathlib.Path) -> None:
    cookies_lines = [
        "# Netscape HTTP Cookie File",
        "# This is a generated file! Do not edit.",
        "",
    ]
    for name, value in credential.model_dump().items():
        if value:
            cookies_lines.append(
                f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name.upper()}\t{value}"
            )
    path.write_text("\n".join(cookies_lines))
