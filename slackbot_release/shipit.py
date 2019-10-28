import logging
from slackbot_release.utils import get

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

async def get_shipit_releases(config, logger=LOGGER):
     url = "https://shipit-api.mozilla-releng.net/releases"
     releases = await get(url)
     return [release for release in releases if release["product"] not in config["ignored_products"]]
