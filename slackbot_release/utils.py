import aiohttp
import logging
import json
import os
import sys

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

def get_config(logger=LOGGER):
    config = {}
    abs_config_path = os.path.join(
        os.path.abspath(os.path.join(os.path.realpath(__file__), '..', '..')),
        "secrets.json"
    )
    if not os.path.exists(abs_config_path):
        LOGGER.critical(f"Couldn't find secret config file. Tried looking in: {abs_config_path}")
        sys.exit()
    with open(abs_config_path) as f:
        config = json.load(f)
    config["ignored_products"] = ["thunderbird"]
    return config

