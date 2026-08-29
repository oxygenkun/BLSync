"""HTTP schemas and secret-safe representation for configuration UIs."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from blsync.configuration.models import (
    EDITABLE_FIELDS,
    Config,
    ConfigCredential,
)


class FieldType(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    SELECT = "select"
    SECRET = "secret"
    FAVORITE_LIST = "favorite-list"


class ConfigFieldSchema(BaseModel):
    key: str
    label: str
    type: FieldType
    description: str | None = None
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] | None = None
    required: bool | None = None
    item_fields: list["ConfigFieldSchema"] | None = None
    postprocess_actions: list[str] | None = None


class ConfigSectionSchema(BaseModel):
    key: str
    title: str
    description: str
    fields: list[ConfigFieldSchema]


class ConfigSystemInfo(BaseModel):
    config_file: str
    data_path: str


class ConfigDocument(BaseModel):
    revision: str
    values: dict[str, Any]
    secret_status: dict[str, bool]
    sections: list[ConfigSectionSchema]
    overridden_fields: list[str]
    system: ConfigSystemInfo


CONFIG_SECTIONS = [
    ConfigSectionSchema(
        key="runtime",
        title="运行与下载",
        description="控制扫描、并发、超时和重试策略。保存后应用到后续任务。",
        fields=[
            ConfigFieldSchema(
                key="interval",
                label="扫描间隔",
                type=FieldType.INTEGER,
                unit="秒",
                min=1,
                description="两次收藏夹扫描之间的等待时间。",
            ),
            ConfigFieldSchema(
                key="request_timeout",
                label="请求超时",
                type=FieldType.INTEGER,
                unit="秒",
                min=1,
                description="访问 Bilibili 接口时的最长等待时间。",
            ),
            ConfigFieldSchema(
                key="log_level",
                label="日志级别",
                type=FieldType.SELECT,
                options=[
                    "TRACE",
                    "DEBUG",
                    "INFO",
                    "SUCCESS",
                    "WARNING",
                    "ERROR",
                    "CRITICAL",
                ],
                description="控制服务端输出的日志详细程度。",
            ),
            ConfigFieldSchema(
                key="retry_failed_tasks",
                label="自动重试失败任务",
                type=FieldType.BOOLEAN,
                description="扫描收藏夹时，将失败任务重新放回待下载队列。",
            ),
            ConfigFieldSchema(
                key="max_concurrent_tasks",
                label="最大并发任务数",
                type=FieldType.INTEGER,
                min=1,
                max=64,
                description="降低后不会中断运行中的任务；提高后立即允许更多任务启动。",
            ),
            ConfigFieldSchema(
                key="task_timeout",
                label="单任务超时",
                type=FieldType.INTEGER,
                unit="秒",
                min=1,
            ),
            ConfigFieldSchema(
                key="download_retry_limit",
                label="分块重试上限",
                type=FieldType.INTEGER,
                min=0,
                max=100,
            ),
            ConfigFieldSchema(
                key="download_stall_timeout",
                label="下载停滞超时",
                type=FieldType.NUMBER,
                unit="秒",
                min=0.1,
                step=0.1,
            ),
            ConfigFieldSchema(
                key="download_url_refresh_retries",
                label="播放地址刷新次数",
                type=FieldType.INTEGER,
                min=0,
                max=20,
            ),
        ],
    ),
    ConfigSectionSchema(
        key="credential",
        title="账号凭据",
        description="凭据只可覆盖或清空，接口不会返回已保存的明文。留空代表保持不变。",
        fields=[
            ConfigFieldSchema(
                key="credential.sessdata", label="SESSDATA", type=FieldType.SECRET
            ),
            ConfigFieldSchema(
                key="credential.bili_jct", label="bili_jct", type=FieldType.SECRET
            ),
            ConfigFieldSchema(
                key="credential.buvid3", label="buvid3", type=FieldType.SECRET
            ),
            ConfigFieldSchema(
                key="credential.dedeuserid",
                label="DedeUserID",
                type=FieldType.SECRET,
            ),
            ConfigFieldSchema(
                key="credential.ac_time_value",
                label="ac_time_value",
                type=FieldType.SECRET,
            ),
        ],
    ),
    ConfigSectionSchema(
        key="favorite_list",
        title="收藏夹同步",
        description="以稳定的任务名管理收藏夹、保存路径、命名模板和下载后操作。",
        fields=[
            ConfigFieldSchema(
                key="favorite_list",
                label="收藏夹",
                type=FieldType.FAVORITE_LIST,
                item_fields=[
                    ConfigFieldSchema(
                        key="name",
                        label="任务名",
                        type=FieldType.STRING,
                        required=True,
                    ),
                    ConfigFieldSchema(
                        key="fid",
                        label="收藏夹 ID",
                        type=FieldType.INTEGER,
                        required=True,
                    ),
                    ConfigFieldSchema(
                        key="path",
                        label="下载路径",
                        type=FieldType.STRING,
                        required=True,
                    ),
                    ConfigFieldSchema(
                        key="name_template",
                        label="文件名模板",
                        type=FieldType.STRING,
                    ),
                    ConfigFieldSchema(
                        key="name_group",
                        label="分组名模板",
                        type=FieldType.STRING,
                    ),
                ],
                postprocess_actions=["move", "remove", "save"],
            )
        ],
    ),
]


def build_config_document(config: Config, revision: str) -> ConfigDocument:
    values = {
        key: value
        for key, value in config.model_dump(mode="json").items()
        if key in EDITABLE_FIELDS
    }
    values["credential"] = {key: None for key in ConfigCredential.model_fields}
    overridden_fields: list[str] = []
    return ConfigDocument(
        revision=revision,
        values=values,
        secret_status={
            key: bool(value) for key, value in config.credential.model_dump().items()
        },
        sections=CONFIG_SECTIONS,
        overridden_fields=overridden_fields,
        system=ConfigSystemInfo(
            config_file=str(config.config_file),
            data_path=str(config.data_path),
        ),
    )
