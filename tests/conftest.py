"""Initializes configs and wipes before each test."""

import json
import logging
import sys
import time
from pathlib import Path

import pytest
from confluent_kafka import KafkaException
from confluent_kafka.admin import AdminClient

logger = logging.getLogger(__name__)
logging.getLogger("kafka").setLevel(logging.WARNING)


def pytest_addoption(parser):
    """Allow for --conf from cli."""
    parser.addoption(
        "--conf",
        action="store",
        default=f"{Path(__file__).parent}/helper_files/localenv.json",
        help="full path of kafka conf file",
    )


def pytest_configure(config):
    """Effectively global vars, including some functions."""
    pytest.conf = json.loads(Path(config.getoption("conf")).read_text(encoding="utf-8"))
    pytest.topics = ["kafkareportuno", "kafkareportdue"]


def wipe_topics(admin, topics):
    """Wipes kafka topics and waits till done."""
    logger.info("deleting topics %s", topics)
    res = admin.delete_topics(topics, request_timeout=1)
    logger.info("waiting for topics %s to be deleted", topics)
    start = time.time()
    timeout = 30
    for _, f in res.items():
        logger.info("Waiting for %s to finish deleting...", _)
        while f.running() is True:
            if time.time() > start + timeout:
                logger.critical("Wipe timed out.")
                sys.exit(1)
            time.sleep(1)
        try:
            f.result()
        except KafkaException as e:
            if e.args[0].code() == 3:
                logger.warning("Broker: Unknown topic or partition... %s", _)
            else:
                raise e
    logger.info("Topics wiped.")


@pytest.fixture(autouse=True)
def init_tests():
    """Wipes kafka topics before each test."""
    logger.info("Initializing...")
    admin = AdminClient(pytest.conf)
    logger.info("wiping topics")
    wipe_topics(admin, pytest.topics)
    yield True
