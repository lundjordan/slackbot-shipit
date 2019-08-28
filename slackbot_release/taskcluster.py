import logging

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

def add_taskcluster_status(phase, logger=LOGGER):
    logger.debug("release status phase: {phase}")
    return {}
