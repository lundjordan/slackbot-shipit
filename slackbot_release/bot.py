import asyncio
from collections import namedtuple
import copy
import logging
import json
import os
import re
import signal
import sys
import urllib.parse

import slack
from taskcluster.exceptions import TaskclusterRestFailure

from slackbot_release.tc import get_tc_group_status, task_is_complete, get_artifact_url
from slackbot_release.tc import graph_is_complete
from slackbot_release.utils import get_config, release_in_message
from slackbot_release.db import update_releases, task_tracked, update_tasks_in_thread
from slackbot_release.db import track_slack_thread, mark_phase_as_done, delete_old_threads, create_db

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

### config
CONFIG = get_config()

async def post_message(text, thread=None, config=CONFIG):
    """
    Posts a direct message in Slack.

    As opposed to a block based message with sections, actions, texts, and dividers.
    """
    message = {
        "channel": "#releng-notifications",
        "icon_emoji": ":sailboat:",
        "text": text,
    }
    if thread:
        message["thread_ts"] = thread

    slack_client = slack.WebClient(token=config["slack_api_token"], run_async=True)

    LOGGER.info(message)
    await slack_client.chat_postMessage(**message)

def add_a_block(message, block_item):
    message = copy.deepcopy(message)
    message["blocks"] = message.get("blocks", [])
    message["blocks"].append(block_item)
    return message

def add_section(section_text):
    return { "type": "section", "text": { "type": "mrkdwn", "text": section_text } }

def add_actions(actions):
    return { "type": "actions", "elements": actions }

def add_button(button_text, button_url):
    return { "type": "button", "text": { "type": "plain_text", "text": button_text }, "url": button_url }

def add_divider():
    return {"type": "divider"}

def expand_slack_payload(**payload):
    data = payload["data"]
    message = data.get("text", "")
    web_client = payload["web_client"]

    return data, message, web_client

def add_signoff_status(reply, release, logger=LOGGER):
    reply = add_a_block(reply, add_section(f"Status: *{release.name}*"))
    for phase in release.phases:
        phase_name = re.sub("(_firefox|_thunderbird|_fennec)", "", phase.name)
        state = ":passport_control:"
        graph_status = "not started"
        tc_button = None

        if phase.triggered:
            state = ":white_check_mark:"

            if phase.groupid:
                tc_button = add_button("Taskcluster", f"https://taskcluster-ui.herokuapp.com/tasks/{phase.groupid}")
                if phase.done:
                    graph_status = "complete"
                else:
                    graph_status = "in progress"

        reply = add_a_block(reply, add_section(f"* {phase_name} - Signed off: {state} - Graph status: {graph_status}"))

        if tc_button:
            reply = add_a_block(reply, add_actions([tc_button]))

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

async def add_tc_group_status(reply, release, phase, group_status, config=CONFIG):
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

    reply = add_a_block(reply, add_section(f"{phase} - detailed status"))
    reply = add_a_block(reply, add_section(f"*{percent}% resolved* - {total} total tasks"))
    reply = add_a_block(reply, add_section(f"{unscheduled} tasks unscheduled"))
    reply = add_a_block(reply, add_section(f"{pending} tasks pending"))
    reply = add_a_block(reply, add_section(f"{running} tasks running"))
    reply = add_a_block(reply, add_section(f"{failed + exception} tasks stuck"))

    if failed or exception:
        reply = add_a_block(reply, add_section("*Stuck Tasks:*"))

        stuck_tasks = group_status["failed"] + group_status["exception"]
        overflow_stuck_tasks = []
        if len(stuck_tasks) > 12:
            # slack limits block messages into max 50 messages.
            # right now we have three messages per stuck task plus additional status.
            overflow_stuck_tasks = stuck_tasks[12:]
            stuck_tasks = stuck_tasks[:12]

        for task in stuck_tasks:
            reply = add_a_block(reply, add_section(f"{task.label} - {task.worker_type} - {task.taskid}"))

            tc_button = add_button("Taskcluster", f"https://taskcluster-ui.herokuapp.com/tasks/{task.taskid}")
            tc_log_url = await get_artifact_url(task.taskid, "public/logs/live_backing.log", config)
            tc_log_button = add_button("Taskcluster Log", tc_log_url)
            th_button = add_button("Treeherder",
                                   f"https://treeherder.mozilla.org/#/jobs?repo={release.repo}&resultStatus"
                                   f"=testfailed%2Cbusted%2Cexception%2Cretry%2Cusercancel%2Crunning%2Cpending"
                                   f"%2Crunnable&searchStr={urllib.parse.quote(task.label, safe='')}"
                                   f"&revision={release.revision}")
            reply = add_a_block(reply, add_actions([tc_button, tc_log_button, th_button]))
            reply = add_a_block(reply, add_divider())
        if overflow_stuck_tasks:
            reply = add_a_block(reply, add_section(f"Other stuck tasks: (trimmed after 3000 chars)"))
            reply = add_a_block(reply, add_section(", ".join(task.taskid for task in overflow_stuck_tasks)[:3000]))

    return reply

async def add_phase_status(reply, release, phase, tc_group_status=None, config=CONFIG, logger=LOGGER):
    reply = add_a_block(reply, add_divider())
    if tc_group_status:
        reply = await add_tc_group_status(reply, release, phase, tc_group_status, config)
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
        "* every 2 min the bot will check for active releases in Shipit and ping r.eleaseduty if a phase's "
        "Taskcluster graph has one or more stuck tasks."
    ))
    reply = add_a_block(reply, add_divider())
    return reply


async def periodic_stuck_tasks_status(config=CONFIG, logger=LOGGER):
    while True:
        logger.info("Checking periodic stuck tasks")
        releases = await update_releases(config=CONFIG)  # poll and sync with shipit live state
        for release in releases:
            for thread in release.slack_threads:
                stuck_tasks = []
                for taskid in thread.tasks:
                    if await task_is_complete(taskid, config):
                        await post_message(f"{taskid} is now green!", thread=thread.threadid)
                    else:
                        stuck_tasks.append(taskid)
                update_tasks_in_thread(thread.threadid, stuck_tasks)
            # scrub threads that have no stuck tasks remaining
            delete_old_threads(release.name)
        await asyncio.sleep(300)


async def periodic_releases_status(config=CONFIG, logger=LOGGER):
    message_template = {
        "channel": "#releng-notifications",
        "icon_emoji": ":sailboat:",
    }
    while True:
        logger.info("Checking periodic release status")
        slack_client = slack.WebClient(token=config["slack_api_token"], run_async=True)
        releases = await update_releases(config=CONFIG)  # poll and sync with shipit live state
        for release in releases:
            stuck_release_message = copy.deepcopy(message_template)
            signoff_status = add_signoff_status(stuck_release_message, release)

            active_phases = [p for p in release.phases if p.triggered and p.groupid and not p.done]

            for phase in active_phases:
                try:
                    tc_group_status = await get_tc_group_status(phase.groupid, config)
                except TaskclusterRestFailure as e:
                    await post_message(f"{release.name} with groupid {phase.groupid} not found")
                    continue  # on to the next release

                # strip tasks that have already been reported
                import pdb; pdb.set_trace()
                tc_group_status["failed"] = [t for t in tc_group_status["failed"] if not task_tracked(t.taskid, release.name)]
                tc_group_status["exception"] = [t for t in tc_group_status["exception"] if not task_tracked(t.taskid, release.name)]

                if tc_group_status["failed"] or tc_group_status["exception"]:

                    await post_message(f"{', '.join(config['releaseduty'])} - {release.name} is stuck!")
                    await slack_client.chat_postMessage(**signoff_status)
                    stuck_release_message = await add_phase_status(
                        stuck_release_message, release, phase.name, tc_group_status
                    )
                    response = await slack_client.chat_postMessage(**stuck_release_message)

                    # start tracking new thread and its tasks so we can keep track of task state
                    track_slack_thread(
                        threadid=response.get("ts"),
                        tasks=[t.taskid for t in tc_group_status["failed"] + tc_group_status["exception"]],
                        release_name=release.name
                    )

                if graph_is_complete(tc_group_status) and not phase.done:
                    mark_phase_as_done(phase.name, release.name)
                    await post_message(f"{', '.join(config['releaseduty'])} - {release.name} phase {phase.name} is complete.")

        await asyncio.sleep(120)

@slack.RTMClient.run_on(event="message")
async def receive_message(**payload):

    data, message, web_client = expand_slack_payload(**payload)

    # template reply
    reply = {
        "channel": data["channel"],
        "thread": data["ts"],
        "icon_emoji": ":sailboat:",
    }

    # TODO should probably use regex or click to parse commands
    message = message.lower()
    if message.startswith("shipit"):
        releases = await update_releases(config=CONFIG)  # poll and sync with shipit live state
        if "shipit status" == message:
            # overall status
            reply = add_overall_shipit_status(reply, releases)
            await web_client.chat_postMessage(**reply)
        elif "shipit status" in message and len(message.split()) == 3:
            # a more detailed specific release status
            for release in releases:
                if release_in_message(release.name, message, CONFIG):
                    signoff_status = add_signoff_status(reply, release)
                    await web_client.chat_postMessage(**signoff_status)
                    for phase in release.phases:
                        if phase.groupid and not phase.done:
                            try:
                                tc_group_status = await get_tc_group_status(phase.groupid, CONFIG)
                            except TaskclusterRestFailure as e:
                                await post_message(f"{release.name} with groupid {phase.groupid} not found")
                                continue  # on to the next phase
                            phase_status = await add_phase_status(reply, release, phase.name, tc_group_status)
                            await web_client.chat_postMessage(**phase_status)

                    break
            else:
                reply = add_a_block(reply, add_section("No matching release status could be found. Message `shipit help` for usage"))
                reply = add_a_block(reply, add_divider())
                await web_client.chat_postMessage(**reply)
        elif "shipit help" == message:
            reply = add_bot_help(reply)
            await web_client.chat_postMessage(**reply)
        else:
            reply = add_a_block(reply, add_section("Sorry, I don't understand. Try messaging `shipit help` for usage"))
            await web_client.chat_postMessage(**reply)


async def main():
    create_db()
    # real-time-messaging Slack client
    client = slack.RTMClient(token=CONFIG["slack_api_token"], run_async=True)
    # periodically check the taskcluster group status of every release in flight
    periodic_releases_status_task = asyncio.create_task(periodic_releases_status())
    periodic_stuck_tasks_status_task = asyncio.create_task(periodic_stuck_tasks_status())

    await asyncio.gather(client.start(),
                         periodic_releases_status_task,
                         periodic_stuck_tasks_status_task)


if __name__ == "__main__":
    asyncio.run(main())
