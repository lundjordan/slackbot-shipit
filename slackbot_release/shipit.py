import logging
from slackbot_release.utils import get

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

async def get_releases(config, logger=LOGGER):
    url = "https://shipit-api.mozilla-releng.net/releases"
    releases = await get(url)
    releases = [release for release in releases if release["product"] not in config["ignored_products"]]

