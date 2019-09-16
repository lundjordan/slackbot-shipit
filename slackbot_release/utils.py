import aiohttp
from collections import namedtuple
import logging
import json
import os
import sys

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

### config
CONFIG = get_config()

# TODO use persistence for these
TRACKED_RELEASES = []
TRACKED_RELEASE = "name, active_phase, threads"
THREADS = []
OLD_THREAD = "threadid, tasks"

def release_in_message(release, message, config):
    """
    Searches for release in the string message.

    This allows for substring matches. e.g. "devedition" would match "devedition-70.0b5-build1"
    Downside is if there is a beta and a release in-flight, you would have to be more
    specific than: "firefox"

    Parameters
    __________
    release: str
        the release name
    message: str
        the full message

    Returns
    _______
    bool
        True if release in message
    """
    target_release = message.split()[-1] # third arg in message. e.g. "devedition-70.0b5-build1"

    return target_release in release.lower()

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
        "staging_secrets.json"
    )
    if not os.path.exists(abs_config_path):
        LOGGER.critical(f"Couldn't find secret config file. Tried looking in: {abs_config_path}")
        sys.exit()
    with open(abs_config_path) as f:
        config = json.load(f)
    config["ignored_products"] = ["thunderbird"]
    return config

