import aiohttp
import sys
import logging

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

async def get(url, logger=LOGGER):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                logger.error("Could not complete request. Are you connected to the VPN?")
                logger.error(f"Failed to GET {response.url}: {response.status}; body={(await response.text())[:1000]}")
                sys.exit()
            response = await response.json()

    return response


