"""Routes that serve the built frontend and its client-side routes."""

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
elif configured_base_dir := os.environ.get("BLSYNC_BASE_DIR"):
    BASE_DIR = Path(configured_base_dir)
else:
    BASE_DIR = Path(__file__).parents[3]
STATIC_DIR = BASE_DIR / "static"

router = APIRouter(tags=["前端"])


@router.get("/", summary="前端页面")
async def read_root() -> FileResponse:
    """Return the frontend application entry page."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend page not found")


@router.get("/{full_path:path}", summary="SPA 路由")
async def spa_fallback(full_path: str) -> FileResponse:
    """Serve static assets or fall back to the SPA entry page."""
    if full_path.startswith("assets/"):
        asset_path = STATIC_DIR / full_path
        if asset_path.exists() and asset_path.is_file():
            return FileResponse(str(asset_path))

    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend page not found")
