import pytest

from blsync.configuration.loader import build_config, parse_args
from blsync.configuration.models import Config
from blsync.scraper import BScraper


@pytest.fixture
def my_config():
    return build_config(parse_args([]).config)


@pytest.mark.asyncio
async def test_get_bvids_from_favid(my_config: Config):
    fid = 3079437303
    bs = BScraper(my_config)

    result = [x async for x in bs._get_bvids_from_favid(str(fid))]
    print(list(result))
    assert len(result) > 0


@pytest.mark.asyncio
async def test_get_all_bvids(my_config: Config):
    bs = BScraper(my_config)

    async for bvid, favid in bs.get_all_bvids():
        print(f"{favid}: {bvid}")
        assert isinstance(bvid, str)
        assert isinstance(favid, str)
