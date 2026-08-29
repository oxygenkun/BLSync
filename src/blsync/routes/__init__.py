"""FastAPI route composition for the BLSync application."""

from fastapi import APIRouter

from blsync.routes.config import router as config_router
from blsync.routes.files import router as file_router
from blsync.routes.frontend import router as frontend_router
from blsync.routes.tasks import router as task_router
from blsync.routes.video import router as video_router

api_router = APIRouter()
api_router.include_router(task_router)
api_router.include_router(video_router)
api_router.include_router(config_router)

__all__ = ["api_router", "file_router", "frontend_router"]
