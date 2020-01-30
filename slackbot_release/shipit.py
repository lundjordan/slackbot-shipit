import logging
from slackbot_release.utils import get

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

async def get_shipit_releases(config, logger=LOGGER):
     releases = await get(config["shipit_url"])
     return [release for release in releases if release["product"] not in config["ignored_products"]]
