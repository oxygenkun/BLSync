"""HTTP adapter for configuration use cases."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from blsync.configuration.store import (
    ConfigRevisionConflict,
    ConfigUpdateInvalid,
    config_store,
)
from blsync.routes.schemas.config import ConfigDocument, build_config_document

router = APIRouter(prefix="/config", tags=["配置"])


class ConfigUpdateRequest(BaseModel):
    revision: str
    changes: dict[str, Any]


@router.get("", summary="读取可编辑配置与表单元数据")
async def get_config_document() -> ConfigDocument:
    snapshot = config_store.get_snapshot()
    return build_config_document(snapshot.config, snapshot.revision)


@router.patch("", summary="校验、写入并应用配置")
async def patch_config_document(request: ConfigUpdateRequest) -> ConfigDocument:
    try:
        snapshot = await config_store.update(request.revision, request.changes)
        return build_config_document(snapshot.config, snapshot.revision)
    except ConfigRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigUpdateInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
