import pytest

from bili_sync_py.configs import load_configs, Config
from bili_sync_py.scraper import BScraper


@pytest.fixture
def my_config():
    return load_configs([])


@pytest.mark.asyncio
async def test_get_bvids_from_favid(my_config: Config):
    fid = 3079437303
    bs = BScraper(my_config)

    result = [x async for x in bs._get_bvids_from_favid(fid)]
    print(list(result))
    assert len(result) > 0
    assert result == ["BV1dsmbYKEBo", "BV1wFmeYZEWB", "BV1hWmpYkEBK"]


@pytest.mark.asyncio
async def test_get_all_bvids(my_config: Config):
    bs = BScraper(my_config)

    async for bvid, favid in bs.get_all_bvids():
        print(f"{favid}: {bvid}")
        assert isinstance(bvid, str)
        assert isinstance(favid, str)
