import logging
from slackbot_release.utils import get

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

async def get_releases(logger=LOGGER):
    url = "https://shipit-api.mozilla-releng.net/releases"
    return await get(url)

