import logging
from slackbot_release.utils import get

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

async def get_releases(tracked_releases, config, logger=LOGGER):
    url = "https://shipit-api.mozilla-releng.net/releases"
    releases = await get(url)
    releases = [release for release in releases if release["product"] not in config["ignored_products"]]

    # delete old tracked releases no longer in shipit
    tracked_releases = {
        t_r:tracked_releases[t_r] for t_r in tracked_releases if t_r in [r["name"] for r in releases]
    }

    for release in releases:
        if not tracked_releases.get(release["name"]):
            tracked_releases[release["name"]] = {
                "name": release["name"],
                "product": release["product"],
                "version": release["version"],
                "repo": release["project"],
                "revision": release["revision"],
                "triggered_phases": [],
                "untriggered_phases": [],
                "current_phase": {},
                "slack_threads": [],
            }

        tracked_release = tracked_releases[release["name"]]

        # reset phases and take shipit's current status
        tracked_release["triggered_phases"] = []
        tracked_release["untriggered_phases"] = []

        for phase in release["phases"]:
            tracked_phase = {
                "name": phase["name"],
                "groupid": phase["actionTaskId"],
            }
            if phase.get("completed"):
                tracked_release["triggered_phases"].append(tracked_phase)
            else:
                tracked_release["untriggered_phases"].append(tracked_phase)

        # bug: a phase can be started before previous graph is finished.
        # slackbot will ignore old phases
        if tracked_release["triggered_phases"]:
            current_phase = tracked_release["triggered_phases"][-1]
            if tracked_release["current_phase"].get("name") != current_phase["name"]:
                tracked_release["current_phase"] = {
                    "name": current_phase["name"],
                    "groupid": current_phase["groupid"],
                    "done": False,
                }
    logger.info("XXX get_releases output")
    logger.info(tracked_releases)

    return tracked_releases
