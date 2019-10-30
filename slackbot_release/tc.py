import asyncio
import aiohttp
from collections import namedtuple
import logging
import os

import taskcluster.aio

# TODO rip this out as part of a standalone group inspector module. Replace graph-progress.sh and tc-filter.py

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

def get_tc_config(config):
    return {
        "rootUrl": config["taskcluster_root_url"],
        "credentials": {
            "clientId": config["taskcluster_client_id"],
            "accessToken": config["taskcluster_access_token"],
        },
    }

async def get_artifact_url(taskid, artifact, config):
    tc_config = get_tc_config(config)
    async with aiohttp.ClientSession() as tc_session:
        queue = taskcluster.aio.Queue(options=tc_config, session=tc_session)
        return queue.buildUrl('getLatestArtifact', taskid, artifact)


async def task_is_complete(taskid, config, logger=LOGGER):
    tc_config = get_tc_config(config)
    async with aiohttp.ClientSession() as tc_session:
        queue = taskcluster.aio.Queue(options=tc_config, session=tc_session)
        status = await queue.status(taskid)
    return (status["status"].get("resolved")) and (status["status"]["state"] in ["completed"])


async def get_tc_group_status(graph_id, config, logger=LOGGER):

    # reimplements tc-filter.py show_filtered
    tc_config = get_tc_config(config)

    filtered_tasks = []

    async with aiohttp.ClientSession() as tc_session:
        queue = taskcluster.aio.Queue(options=tc_config, session=tc_session)
        def pagination(y):
            filtered_tasks.extend(y.get('tasks', []))

        await queue.listTaskGroup(graph_id, paginationHandler=pagination)

    group_status = {
        "unscheduled": [],
        "pending": [],
        "running": [],
        "completed": [],
        "failed": [],
        "exception": [],
    }
    Task = namedtuple("Task", ["taskid", "label", "worker_type"])
    for t in filtered_tasks:
        label = t['task'].get('tags', {}).get('label', t['task'].get('metadata').get('name', ''))
        task_status = t['status']
        group_status[task_status['state']].append(
            Task(task_status['taskId'], label, task_status['workerType'])
        )
    return group_status

def graph_is_complete(tc_group_status):
    return not any([
        tc_group_status["failed"],
        tc_group_status["exception"],
        tc_group_status["running"],
        tc_group_status["pending"],
        tc_group_status["unscheduled"],
    ])
