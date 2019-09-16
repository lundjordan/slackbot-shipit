import asyncio
from collections import namedtuple
import copy
import logging
import json
import os
import re
import signal
import sys

import slack

from slackbot_release.tc import get_tc_group_status
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

def add_tc_group_status(reply, group_status):
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

    if failed or exception:
        reply = add_a_block(reply, add_section("Stuck Tasks:"))
        for task in group_status["failed"]:
            reply = add_a_block(reply, add_section(f"    FAILED: {task.label} - {task.worker_type} - https://taskcluster-ui.herokuapp.com/tasks/{task.taskid}"))
        for task in group_status["exception"]:
            reply = add_a_block(reply, add_section(f"    EXCEPTION: {task.label} - {task.worker_type} - https://taskcluster-ui.herokuapp.com/tasks/{task.taskid}"))
    return reply

def add_detailed_release_status(reply, release, tc_group_status, only_stuck=False, config=CONFIG, logger=LOGGER):
    """
    only_stuck: if True, return None if the release does not have any stuck (failed or exception) tasks
    """
    name = release["name"]
    reply = add_a_block(reply, add_divider())
    reply = add_signoff_status(reply, release)
    reply = add_tc_group_status(reply, tc_group_status)

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
        "channel": "#releng-notifications",
        "icon_emoji": ":sailboat:",
        "blocks": [],
    }
    while True:
        logger.debug("Checking periodic release status")
        slack_client = slack.WebClient(token=config["slack_api_token"], run_async=True)
        releases = await get_releases()
        for release in releases:
            stuck_release_message = copy.deepcopy(message_template)
            stuck_release_message = add_a_block(
                stuck_release_message, add_section(f"@releaseduty - {release['name']} is stuck!")
            )

            current_phase = None
            for phase in release["phases"]:
                if phase.get("actionTaskId") and phase.get("completed"):
                    current_phase = phase

            tc_group_status = await get_tc_group_status(current_phase["actionTaskId"], config)
            reporting_stuck_tasks = tc_group_status["failed"] + tc_group_status["exception"]
            if reporting_stuck_tasks:
                repeated_stuck_tasks = [] # list of threads
                for thread in TRACKED_RELEASES[release["name"]]["threads"]:
                    tracked_thread_tasks = [task.taskid for task in thread.tasks]
                    for stuck_task in reporting_stuck_tasks:
                        if stuck_task.taskid in tracked_thread_tasks:
                            # TODO create temp thread if does not exist, add task to list
                            # TODO pop task from tc_group_status
                # TODO for thread in repeated_stuck_threads:
                    # stuck_release_message["thread"] = thread.threadid
                    # for task in thread.tasks
                        #  stuck_release_message = add_a_block(
                            #  stuck_release_message, add_section(f"TASK")
                        #  )
                    # await slack_client.chat_postMessage(**stuck_release_message)
                # TODO send remaining new stuck tasks as separate message and capture threadid
                # if any_remaining_reporting_tasks
                    #  stuck_release_message = add_detailed_release_status(
                        #  stuck_release_message, release, tc_group_status
                    #  )
                    #  response = await slack_client.chat_postMessage(**stuck_release_message)
                    # TODO create a new thread in TRACKED_RELEASE
                
        await asyncio.sleep(300)

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
                if release_in_message(release["name"], message, CONFIG):
                    in_progress_reply = copy.deepcopy(reply)
                    in_progress_reply = add_a_block(reply, add_section(f"Getting Taskcluster status for *{release['name']}*..."))
                    await web_client.chat_postMessage(**in_progress_reply)
                    current_phase = None
                    for phase in release["phases"]:
                        if phase.get("actionTaskId") and phase.get("completed"):
                            current_phase = phase
                    tc_group_status = await get_tc_group_status(current_phase["actionTaskId"], config)
                    reply = add_detailed_release_status(reply, release tc_group_status)
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
