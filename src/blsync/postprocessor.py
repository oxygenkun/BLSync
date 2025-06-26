"""
Post-processing module for handling actions after video download.
支持的操作：
- move: 将视频从一个收藏夹移动到另一个收藏夹
- remove: 从收藏夹中移除视频
"""

from typing import Dict, List, Optional

from bilibili_api import Credential, favorite_list, video
from loguru import logger

from .configs import Config


class PostProcessor:
    """处理下载后的操作"""
    
    def __init__(self, config: Config):
        self.config = config
        self.credential = Credential(
            sessdata=config.credential.sessdata,
            bili_jct=config.credential.bili_jct,
            buvid3=config.credential.buvid3,
            dedeuserid=config.credential.dedeuserid,
            ac_time_value=config.credential.ac_time_value,
        )
        
    async def execute_postprocess_actions(self, bvid: str, favid: str, actions: List[Dict]):
        """
        执行后处理操作
        
        Args:
            bvid: 视频的bvid
            favid: 当前收藏夹ID
            actions: 操作列表，每个操作包含 action 和相关参数
        """
        if not actions:
            return
            
        # 获取视频的aid，因为API需要使用aid
        try:
            v = video.Video(bvid=bvid, credential=self.credential)
            video_info = await v.get_info()
            aid = video_info['aid']
        except Exception as e:
            logger.error(f"Failed to get video info for {bvid}: {e}")
            return
            
        for action in actions:
            try:
                await self._execute_single_action(aid, favid, action)
            except Exception as e:
                logger.error(f"Failed to execute action {action} for video {bvid}: {e}")
                
    async def _execute_single_action(self, aid: int, current_favid: str, action: Dict):
        """
        执行单个操作
        
        Args:
            aid: 视频的aid
            current_favid: 当前收藏夹ID
            action: 操作配置
        """
        action_type = action.get("action")
        
        if action_type == "move":
            target_fid = action.get("fid")
            if not target_fid:
                logger.error(f"Move action missing target fid: {action}")
                return
                
            await self._move_video(aid, int(current_favid), int(target_fid))
            logger.info(f"Moved video {aid} from fav {current_favid} to fav {target_fid}")
            
        elif action_type == "remove":
            await self._remove_video(aid, int(current_favid))
            logger.info(f"Removed video {aid} from fav {current_favid}")
            
        else:
            logger.warning(f"Unknown action type: {action_type}")
            
    async def _move_video(self, aid: int, from_fid: int, to_fid: int):
        """
        将视频从一个收藏夹移动到另一个收藏夹
        
        Args:
            aid: 视频aid
            from_fid: 源收藏夹ID
            to_fid: 目标收藏夹ID
        """
        try:
            result = await favorite_list.move_video_favorite_list_content(
                media_id_from=from_fid,
                media_id_to=to_fid,
                aids=[aid],
                credential=self.credential
            )
            
            if result != 0:
                raise Exception(f"API returned error: {result}")
                
        except Exception as e:
            logger.error(f"Failed to move video {aid} from {from_fid} to {to_fid}: {e}")
            raise
            
    async def _remove_video(self, aid: int, fid: int):
        """
        从收藏夹中移除视频
        
        Args:
            aid: 视频aid
            fid: 收藏夹ID
        """
        try:
            result = await favorite_list.delete_video_favorite_list_content(
                media_id=fid,
                aids=[aid],
                credential=self.credential
            )
            
            if result != 0:
                raise Exception(f"API returned error: {result}")
                
        except Exception as e:
            logger.error(f"Failed to remove video {aid} from {fid}: {e}")
            raise
            
    def get_postprocess_actions(self, favid: str) -> Optional[List[Dict]]:
        """
        获取指定收藏夹的后处理操作配置
        
        Args:
            favid: 收藏夹ID
            
        Returns:
            操作列表，如果没有配置则返回None
        """
        # 查找favorite_list配置中是否有对应的task配置
        for key, value in self.config.favorite_list.items():
            if isinstance(value, dict) and value.get("fid") == int(favid):
                return value.get("postprocess", [])
        
        return None
