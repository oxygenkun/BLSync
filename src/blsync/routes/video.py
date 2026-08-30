"""Routes for retrieving Bilibili video metadata."""

from fastapi import APIRouter, HTTPException, Query

from blsync.configuration.store import get_config
from blsync.scraper import BScraper

router = APIRouter(prefix="/video", tags=["视频"])


@router.get("/info", summary="获取视频详细信息")
async def get_video_info(bvid: str = Query(..., description="视频BV号")):
    """Return video, owner, and episode metadata for a BV id."""
    scraper = BScraper(get_config())
    video_info = await scraper.get_video_info(bvid)
    if video_info is None:
        raise HTTPException(status_code=404, detail="视频不存在或已失效")

    return {
        "bvid": bvid,
        "title": video_info.get("title"),
        "pic": video_info.get("pic"),
        "desc": video_info.get("desc"),
        "videos": video_info.get("videos", 1),
        "pages": video_info.get("pages", []),
        "owner": {
            "name": video_info.get("owner", {}).get("name"),
            "face": video_info.get("owner", {}).get("face"),
        },
    }
