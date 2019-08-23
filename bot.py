import asyncio
import aiohttp
import logging
import json
import os
import sys
import copy

import slack

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

### configuration
if "SLACK_API_TOKEN" not in os.environ:
    LOGGER.critical("bot requires SLACK_API_TOKEN in your ENV")
    sys.exit()
TOKEN = os.environ["SLACK_API_TOKEN"]

def add_a_block(message, block_item):
    message = copy.deepcopy(message)
    message["blocks"].append(block_item)
    return message

def add_section(section_text):
    return { "type": "section", "text": { "type": "mrkdwn", "text": section_text } }

def add_divider():
    return {"type": "divider"}

def expand_slack_payload(**payload):
    data = payload["data"]
    message = data["text"]
    web_client = payload["web_client"]

    return data, message, web_client

async def get(url, logger=LOGGER):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                logger.error("Could not complete request. Are you connected to the VPN?")
                logger.error(f"Failed to GET {response.url}: {response.status}; body={(await response.text())[:1000]}")
            response = await response.json()

    return response

async def get_status(reply, logger=LOGGER):
    url = "https://shipit-api.mozilla-releng.net/releases"
    releases = await get(url)

    # compose message status
    reply = add_a_block(reply, add_section("Releases in-flight:"))
    reply = add_a_block(reply, add_divider())

    for release in releases:
        reply = add_a_block(reply, add_section(f"*{release['name']}*"))
        reply = add_a_block(reply, add_section("Status:"))
        for phase in release["phases"]:
            state = "requires signoff"
            if phase["submitted"]:
                if phase["completed"]:
                    state = ":white_check_mark:"
                else:
                    state = ":arrows_counterclockwise:"

            reply = add_a_block(reply, add_section(f"\tPhase: {phase['name']} - {state}"))
        reply = add_a_block(reply, add_divider())
    from pprint import pformat
    logger.info(pformat(reply))


@slack.RTMClient.run_on(event="message")
async def receive_message(**payload):

    data, message, web_client = expand_slack_payload(payload)

    # template reply
    reply = {
        "channel": data["channel"],
        "thread": data["ts"],
        "icon_emoji": ":sailboat:",
        "blocks": [],
    }

    if "status" == message.lower():
        reply = get_status(reply, data, web_client)

    if reply["channel"] != "ID?": # TODO find out the ID for releaseduty
        LOGGER.error("Can only reply to the #releaseduty channel. Is this bot coming from another channel?")
        sys.exit()

    await web_client.chat_postMessage(reply)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)

    # real-time-messaging Slack client
    client = slack.RTMClient(token=TOKEN, run_async=True, loop=loop)
    loop.run_until_complete(client.start())
