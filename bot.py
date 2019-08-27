import asyncio
import aiohttp
import logging
import json
import os
import re
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
    message = data.get("text", "")
    web_client = payload["web_client"]

    return data, message, web_client

async def get(url, logger=LOGGER):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                logger.error("Could not complete request. Are you connected to the VPN?")
                logger.error(f"Failed to GET {response.url}: {response.status}; body={(await response.text())[:1000]}")
                sys.exit()
            response = await response.json()

    return response

async def get_releases(logger=LOGGER):
    url = "https://shipit-api.mozilla-releng.net/releases"
    return await get(url)


def add_release_signoff_status(reply, release, logger=LOGGER):
    reply = add_a_block(reply, add_section(f"Status: *{release['name']}*"))
    for phase in release["phases"]:
        # strip the product name out of the phase for presenting
        phase_name = re.sub("(_firefox|_thunderbird|_fennec)", "", phase["name"])
        state = ":passport_control:"
        tc_url = ""
        if phase["submitted"] and phase["completed"]:
            state = ":white_check_mark:"
            tc_url = f"https://taskcluster-ui.herokuapp.com/tasks/{phase['actionTaskId']}"

        reply = add_a_block(reply, add_section(f"    {phase_name} - {state} {tc_url}"))
    reply = add_a_block(reply, add_divider())
    return reply

def add_overall_status(reply, releases, logger=LOGGER):
    # compose message status
    reply = add_a_block(reply, add_section("Releases in-flight:"))
    reply = add_a_block(reply, add_divider())

    for release in releases:
        reply = add_release_signoff_status(reply, release)
    return reply

def add_release_status(reply, release, logger=LOGGER):
    name = release["name"]
    reply = add_a_block(reply, add_section("Release {name} status:"))
    reply = add_release_signoff_status(reply, release)

    for phase in release["phases"]:
        if phase["actionTaskId"] and not phase["completed"]:
            pass # TODO get progress of taskcluster groupid

def add_bot_help(reply):
    return reply # TODO

@slack.RTMClient.run_on(event="message")
async def receive_message(**payload):

    data, message, web_client = expand_slack_payload(**payload)

    # template reply
    reply = {
        "channel": data["channel"],
        "thread": data["ts"],
        "icon_emoji": ":sailboat:",
        "blocks": [],
    }

    LOGGER.debug(f"message: {message}")
    if  message.lower().startswith("shipit"):
        releases = await get_releases()
        # TODO should probably use regex or click to parse commands
        if "shipit status" == message.lower():
            # overall status
            reply = add_overall_status(reply, releases)
        elif "shipit status" in message.lower():
            # a more detailed specific release status
            for release in releases:
                if release["name"] in message:
                    reply = add_release_status(reply, release)
                    break
            else:
                reply = add_a_block(reply, add_section("No matching release status could be found. Message `shipit help` for usage"))
                reply = add_a_block(reply, add_divider())
        else:
            reply = add_a_block(reply, add_section("Sorry, I don't understand. Try messaging `shipit help` for usage"))

        return await web_client.chat_postMessage(**reply)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)

    # real-time-messaging Slack client
    client = slack.RTMClient(token=TOKEN, run_async=True, loop=loop)
    loop.run_until_complete(client.start())
    loop.run_until_complete(client.start())
