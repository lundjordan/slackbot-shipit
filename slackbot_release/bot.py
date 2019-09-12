import asyncio
import copy
import logging
import json
import os
import re
import sys

import slack

from slackbot_release.tc import get_taskcluster_group_status
from slackbot_release.shipit import get_releases
from slackbot_release.utils import get_config, release_in_message

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

### config
CONFIG = get_config()

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

def add_signoff_status(reply, release, logger=LOGGER):
    reply = add_a_block(reply, add_section(f"Status: *{release['name']}*"))
    for phase in release["phases"]:
        # strip the product name out of the phase for presenting
        phase_name = re.sub("(_firefox|_thunderbird|_fennec)", "", phase["name"])
        state = ":passport_control:"
        tc_url = ""
        if phase["submitted"] and phase["completed"]:
            state = ":white_check_mark:"
            tc_url = f"https://taskcluster-ui.herokuapp.com/tasks/{phase['actionTaskId']}"

        reply = add_a_block(reply, add_section(f"* {phase_name} - {state}\n{tc_url}"))
    reply = add_a_block(reply, add_divider())
    return reply

def add_overall_shipit_status(reply, releases, logger=LOGGER):
    # compose message status
    reply = add_a_block(reply, add_section("Releases in-flight:"))
    reply = add_a_block(reply, add_divider())

    for release in releases:
        reply = add_signoff_status(reply, release)

    if not releases:
        reply = add_a_block(reply, add_section("None!"))
    return reply

def add_taskcluster_group_status(reply, group_status):
    # reimplements graph-progress.sh
    total = sum(len(group_status[k]) for k in group_status)
    unscheduled = len(group_status["unscheduled"])
    pending = len(group_status["pending"])
    running = len(group_status["running"])
    completed = len(group_status["completed"])
    failed = len(group_status["failed"])
    exception = len(group_status["exception"])
    resolved = sum([completed, failed, exception])
    resolved = sum([completed, failed, exception])
    percent = int((resolved / total) * 100)

    reply = add_a_block(reply, add_section(f"*{percent}% resolved*"))
    reply = add_a_block(reply, add_section(f"{total} total tasks"))
    reply = add_a_block(reply, add_divider())
    reply = add_a_block(reply, add_section(f"{unscheduled} tasks unscheduled"))
    reply = add_a_block(reply, add_section(f"{pending} tasks pending"))
    reply = add_a_block(reply, add_section(f"{running} tasks running"))
    reply = add_a_block(reply, add_section(f"{failed} task failures"))
    reply = add_a_block(reply, add_section(f"{exception} task exceptions"))
    reply = add_a_block(reply, add_divider())

    if failed:
        reply = add_a_block(reply, add_section("Failing Tasks:"))
        for task in group_status["failed"]:
            reply = add_a_block(reply, add_section(f"    {task.label} - {task.worker_type} - https://taskcluster-ui.herokuapp.com/tasks/{task.taskid}"))
    if exception:
        reply = add_a_block(reply, add_section("Exception Tasks:"))
        for task in group_status["exception"]:
            reply = add_a_block(reply, add_section(f"    {task.label} - {task.worker_type} - https://taskcluster-ui.herokuapp.com/tasks/{task.taskid}"))
    return reply

async def add_detailed_release_status(reply, release, only_blocked=False, config=CONFIG, logger=LOGGER):
    """
    only_blocked: if True, return None if the release does not have any blocked (failed or exception) tasks
    """
    name = release["name"]
    reply = add_a_block(reply, add_divider())
    reply = add_signoff_status(reply, release)

    signed_off_phases = []
    for phase in release["phases"]:
        if phase["actionTaskId"] and phase["completed"]:
            signed_off_phases.append(phase)
    current_phase = signed_off_phases[-1]  # pop most recent signed off phase
    taskcluster_group_status = await get_taskcluster_group_status(current_phase["actionTaskId"], config)
    if only_blocked and not any([taskcluster_group_status["failed"], taskcluster_group_status["exception"]]):
        reply = None
    else:
        reply = add_taskcluster_group_status(reply, taskcluster_group_status)
    return reply

def add_bot_help(reply):
    reply = add_a_block(reply, add_section("*Supported queries:*"))
    reply = add_a_block(reply, add_section("`shipit status`"))
    reply = add_a_block(reply, add_section(
        ">>> Shows each active release within shipit.mozilla-releng.net. Checks only what phases have been signed off. "
        "Doesn't inspect the Taskcluster graph status within a phase"
    ))
    reply = add_a_block(reply, add_section("`shipit status $release`"))
    reply = add_a_block(reply, add_section(
        ">>> Shows each phase signoff status and inspects the most recent phase's Taskcluster "
        "graph status. Highlighting how far along the graph is and which (if any) tasks are stuck and require attention.\n\n"
        "$release: can be a substring of the full release name. e.g. 'Devedition' would match 'Devedition-70.0b5-build1'"
    ))
    reply = add_a_block(reply, add_divider())
    reply = add_a_block(reply, add_section("*Background tasks (non interactive):*"))
    reply = add_a_block(reply, add_section(
        "* every 2 min the bot will check for active releases in Shipit and ping @releaseduty if a phase's "
        "Taskcluster graph has one or more stuck tasks."
    ))
    reply = add_a_block(reply, add_divider())
    return reply

async def periodic_releases_status(config=CONFIG, logger=LOGGER):
    message_template = {
        "channel": "#releaseduty",
        "icon_emoji": ":sailboat:",
        "blocks": [],
    }
    while True:
        logger.debug("Checking periodic release status")
        slack_client = slack.WebClient(token=config["slack_api_token"], run_async=True)
        releases = await get_releases()
        for release in releases:
            if release["product"] not in config["ignored_products"]:
                blocked_release_message = copy.deepcopy(message_template)
                blocked_release_message = add_a_block(
                    blocked_release_message, add_section(f"@releaseduty - {release['name']} is stuck!")
                )
                blocked_release_message = await add_detailed_release_status(blocked_release_message, release, only_blocked=True)
                logger.debug(f"Checking {release['name']} status.")
                if blocked_release_message:
                    await slack_client.chat_postMessage(**blocked_release_message)
        await asyncio.sleep(120)

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

    # TODO should probably use regex or click to parse commands
    message = message.lower()
    if  message.startswith("shipit"):
        releases = await get_releases()
        if "shipit status" == message:
            # overall status
            reply = add_overall_shipit_status(reply, releases)
        elif "shipit status" in message and len(message.split()) == 3:
            # a more detailed specific release status
            for release in releases:
                if release_in_message(release["name"], release["product"], message, CONFIG):
                    in_progress_reply = copy.deepcopy(reply)
                    in_progress_reply = add_a_block(reply, add_section(f"Getting Taskcluster status for *{release['name']}*..."))
                    await web_client.chat_postMessage(**in_progress_reply)
                    reply = await add_detailed_release_status(reply, release)
                    break
            else:
                reply = add_a_block(reply, add_section("No matching release status could be found. Message `shipit help` for usage"))
                reply = add_a_block(reply, add_divider())
        elif "shipit help" == message:
            reply = add_bot_help(reply)
        else:
            reply = add_a_block(reply, add_section("Sorry, I don't understand. Try messaging `shipit help` for usage"))

        return await web_client.chat_postMessage(**reply)


if __name__ == "__main__":
    # loooop
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)


    # real-time-messaging Slack client
    client = slack.RTMClient(token=CONFIG["slack_api_token"], run_async=True, loop=loop)

    # periodically check the taskcluster group status of every release in flight
    periodic_releases_status_task = loop.create_task(periodic_releases_status())

    loop.run_until_complete(client.start())
    loop.run_until_complete(periodic_releases_status_task)
