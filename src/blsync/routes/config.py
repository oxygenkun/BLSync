"""HTTP adapter for configuration and QR-login use cases."""

import io
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import segno
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from yutto.login import (
    QR_POLL_API,
    QR_STATUS_CONFIRMED,
    QR_STATUS_EXPIRED,
    QR_STATUS_NOT_SCANNED,
    QR_STATUS_SCANNED,
    complete_login,
    generate_qr_login,
    request_json,
)
from yutto.utils.fetcher import create_client

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


class QrLoginStatus(StrEnum):
    PENDING = "pending"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


class QrLoginCreated(BaseModel):
    id: str
    expires_in: int


class QrLoginResult(BaseModel):
    status: QrLoginStatus
    config: ConfigDocument | None = None


@dataclass(slots=True)
class QrLoginSession:
    key: str
    url: str
    expires_at: float
    status: QrLoginStatus = QrLoginStatus.PENDING
    config: ConfigDocument | None = None


QR_LOGIN_TTL_SECONDS = 180
_qr_login_sessions: dict[str, QrLoginSession] = {}


def _qr_login_session(session_id: str) -> QrLoginSession:
    session = _qr_login_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="扫码登录会话不存在")
    if (
        session.status not in {QrLoginStatus.CONFIRMED, QrLoginStatus.EXPIRED}
        and time.monotonic() >= session.expires_at
    ):
        session.status = QrLoginStatus.EXPIRED
    return session


def _discard_stale_qr_sessions() -> None:
    now = time.monotonic()
    stale = [
        session_id
        for session_id, session in _qr_login_sessions.items()
        if now >= session.expires_at + QR_LOGIN_TTL_SECONDS
    ]
    for session_id in stale:
        _qr_login_sessions.pop(session_id, None)


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


@router.post("/auth/qr", summary="创建哔哩哔哩扫码登录会话")
async def create_qr_login() -> QrLoginCreated:
    _discard_stale_qr_sessions()
    try:
        async with create_client(trust_env=True, timeout=10, verify=True) as client:
            login_url, qr_key = await generate_qr_login(client)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"获取登录二维码失败：{exc}") from exc

    session_id = secrets.token_urlsafe(24)
    _qr_login_sessions[session_id] = QrLoginSession(
        key=qr_key,
        url=login_url,
        expires_at=time.monotonic() + QR_LOGIN_TTL_SECONDS,
    )
    return QrLoginCreated(id=session_id, expires_in=QR_LOGIN_TTL_SECONDS)


@router.get("/auth/qr/{session_id}/image", summary="读取扫码登录二维码")
async def get_qr_login_image(session_id: str) -> Response:
    session = _qr_login_session(session_id)
    if session.status == QrLoginStatus.EXPIRED:
        raise HTTPException(status_code=410, detail="二维码已过期")
    output = io.BytesIO()
    segno.make(session.url).save(output, kind="svg", scale=6, border=2)
    return Response(
        content=output.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/auth/qr/{session_id}", summary="查询扫码登录状态")
async def get_qr_login_status(session_id: str) -> QrLoginResult:
    session = _qr_login_session(session_id)
    if session.status in {QrLoginStatus.CONFIRMED, QrLoginStatus.EXPIRED}:
        return QrLoginResult(status=session.status, config=session.config)

    try:
        async with create_client(trust_env=True, timeout=10, verify=True) as client:
            payload = await request_json(
                client,
                QR_POLL_API,
                params={"qrcode_key": session.key, "source": "main-fe-header"},
            )
            data = payload.get("data")
            status = data.get("code") if isinstance(data, dict) else None
            if status == QR_STATUS_NOT_SCANNED:
                return QrLoginResult(status=QrLoginStatus.PENDING)
            if status == QR_STATUS_SCANNED:
                session.status = QrLoginStatus.SCANNED
                return QrLoginResult(status=session.status)
            if status == QR_STATUS_EXPIRED:
                session.status = QrLoginStatus.EXPIRED
                return QrLoginResult(status=session.status)
            if status != QR_STATUS_CONFIRMED or not isinstance(data, dict):
                raise ValueError(f"二维码状态异常：{payload}")
            redirect_url = data.get("url")
            if not isinstance(redirect_url, str):
                raise TypeError("登录成功但未返回跳转链接")
            _, sessdata, bili_jct = await complete_login(client, redirect_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"查询扫码状态失败：{exc}") from exc

    if not sessdata:
        raise HTTPException(status_code=502, detail="登录成功但未获取到 SESSDATA")
    try:
        snapshot = config_store.get_snapshot()
        updated = await config_store.update(
            snapshot.revision,
            {"credential": {"sessdata": sessdata, "bili_jct": bili_jct}},
        )
    except (ConfigRevisionConflict, ConfigUpdateInvalid) as exc:
        raise HTTPException(status_code=409, detail=f"凭证写入失败：{exc}") from exc

    session.status = QrLoginStatus.CONFIRMED
    session.config = build_config_document(updated.config, updated.revision)
    return QrLoginResult(status=session.status, config=session.config)
