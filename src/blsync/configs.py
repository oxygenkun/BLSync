import argparse
import dataclasses
import pathlib

import toml


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
        type=bool,
        default=False,
        help="Show verbose output",
    )

    return parser.parse_args(args)


@dataclasses.dataclass
class ConfigCredential:
    sessdata: str
    bili_jct: str
    buvid3: str
    dedeuserid: str
    ac_time_value: str


@dataclasses.dataclass
class Config:
    config_file: pathlib.Path
    data_path: pathlib.Path

    verbose: bool

    interval: int
    request_timeout: int
    credential: ConfigCredential
    favorite_list: dict


def load_configs(args=None) -> Config:
    """
    Helper function to load all configurations
    from the configuration file and command line.
    """
    # TODO: 命令行参数覆盖配置文件
    args = parse_command_line_args(args)
    toml_config = toml.load(args.config)
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
        credential=ConfigCredential(
            sessdata=toml_config["credential"]["sessdata"],
            bili_jct=toml_config["credential"]["bili_jct"],
            buvid3=(
                toml_config["credential"]
                if "buvid3" in toml_config["credential"]
                and toml_config["credential"] != ""
                else None
            ),
            dedeuserid=(
                toml_config["credential"]["dedeuserid"]
                if "dedeuserid" in toml_config["credential"]
                and toml_config["credential"] != ""
                else None
            ),
            ac_time_value=(
                toml_config["credential"]["ac_time_value"]
                if "ac_time_value" in toml_config["credential"]
                and toml_config["credential"] != ""
                else None
            ),
        ),
        favorite_list=toml_config["favorite_list"],
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
    for name, value in dataclasses.fields(credential):
        if value:
            cookies_lines.append(
                f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name.upper()}\t{value}"
            )
    path.write_text("\n".join(cookies_lines))
