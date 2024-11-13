import argparse
import dataclasses
import pathlib

import toml


def parse_command_line_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bili-sync")
    parser.add_argument(
        "-c",
        "--config",
        type=pathlib.Path,
        default="./config.toml",
        help="Path to the configuration file",
    )

    parser.add_argument(
        "-d",
        "--data",
        type=pathlib.Path,
        default="./data.sqlite3",
        help="Path to the data file",
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
    data_file: pathlib.Path

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
    config = toml.load(args.config)
    return Config(
        config_file=args.config,
        data_file=args.data,
        interval=config["interval"],
        request_timeout=config["request_timeout"],
        credential=ConfigCredential(
            sessdata=config["credential"]["sessdata"],
            bili_jct=config["credential"]["bili_jct"],
            buvid3=config["credential"]["buvid3"],
            dedeuserid=config["credential"]["dedeuserid"],
            ac_time_value=config["credential"]["ac_time_value"],
        ),
        favorite_list=config["favorite_list"],
    )


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
