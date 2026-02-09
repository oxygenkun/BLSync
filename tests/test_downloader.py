from pathlib import Path

import pytest
from blsync.downloader import download_video

from blsync.configs import Config, load_configs


@pytest.fixture
def my_config():
    return load_configs([])


@pytest.mark.asyncio
async def test_download(my_config: Config):
    await download_video("BV1whUoYfENS", Path("./sync"), my_config)
    assert Path(
        "./sync/老龟头汤面，汤好吊老品牌值得信赖，十分甚至九分的好吃.mp4"
    ).exists()
