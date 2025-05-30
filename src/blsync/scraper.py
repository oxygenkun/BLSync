from .configs import Config
from bilibili_api import Credential, favorite_list, video
from loguru import logger


class BScraper:
    config: Config
    credential: Credential

    def __init__(self, config: Config):
        self.config = config
        self.credential = Credential(
            sessdata=config.credential.sessdata,
            bili_jct=config.credential.bili_jct,
            buvid3=config.credential.buvid3,
            dedeuserid=config.credential.dedeuserid,
            ac_time_value=config.credential.ac_time_value,
        )

    async def _get_bvids_from_favid(self, favid: str):
        """
        获取收藏夹下面的所有视频bvid，如果有未下载的新视频会更新字典

        :param media_id: 收藏夹id
        """
        fav_list = favorite_list.FavoriteList(
            media_id=favid, credential=self.credential
        )

        # TODO 增量获取，不会重复获取已经下载的视频
        try:
            ids = await fav_list.get_content_ids_info()
            for id in ids:
                yield id["bvid"]
        except Exception as e:
            logger.exception(e)
            yield None

    async def get_all_bvids(self):
        for favid in self.config.favorite_list.keys():
            if int(favid) < 0:
                continue
            async for bvid in self._get_bvids_from_favid(favid):
                if not bvid:
                    continue
                yield bvid, favid

    async def get_video_info(self, bvid):
        """
        获取视频信息。

        :param media_id: 收藏夹id
        :param bvid: 视频bvid

        Returns:
            dict: title的值为视频标题，pages为视频分p信息，如果pages的长度大于1表示存在多分p
        """
        # 实例化 Video 类，用于获取指定视频信息
        v = video.Video(bvid=bvid, credential=self.credential)
        # 获取视频信息
        info = dict()
        try:
            info["title"] = (await v.get_info())["title"]
            info["pages"] = len((await v.get_info())["pages"])
            info["dynamic"] = (await v.get_info())["dynamic"]
        except Exception:
            # TODO
            # 失效的视频添加到已经下载集合
            # already_download_bvids_add(media_id=media_id, bvid=bvid)
            logger.warning(bvid + "视频失效")
            return None
        return info
