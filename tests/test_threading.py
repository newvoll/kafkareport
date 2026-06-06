"""Threading correctness tests for KafkaReport.watermarks()."""

import logging
import sys
import threading
import time

import pytest
from confluent_kafka import KafkaException
from confluent_kafka.admin import AdminClient, NewPartitions, NewTopic

from kafkareport import KafkaReport

logger = logging.getLogger(__name__)
ADMIN = AdminClient(pytest.conf)
TARGET = KafkaReport(pytest.conf)


def _create_topic(name):
    config = {
        "delete.retention.ms": "16800001",
        "retention.ms": "16800001",
        "retention.bytes": "16800001",
    }
    new_topics = [NewTopic(name, num_partitions=3, replication_factor=1, config=config)]
    fs = ADMIN.create_topics(new_topics, request_timeout=10)
    start = time.time()
    timeout = 30
    for tname, f in fs.items():
        while f.running():
            if time.time() > start + timeout:
                logger.critical("Topic creation timed out.")
                sys.exit(1)
            time.sleep(1)
        try:
            f.result()
        except KafkaException as e:
            logger.critical("Failed to create topic %s: %s", tname, e)
            raise e


def _add_partitions(name):
    fs = ADMIN.create_partitions([NewPartitions(name, 3)])
    start = time.time()
    timeout = 30
    for tname, f in fs.items():
        while f.running():
            if time.time() > start + timeout:
                logger.critical("Partitioning timed out.")
                sys.exit(1)
            time.sleep(1)
        try:
            f.result()
        except KafkaException as e:
            if e.args[0].code() == 37 and "already has" in e.args[0].str():
                pass
            else:
                logger.critical("Failed to partition topic %s: %s", tname, e)
                raise e


@pytest.fixture
def topic_ready():
    name = pytest.topics[0]
    _create_topic(name)
    _add_partitions(name)
    return name


def test_worker_error_surfaces(topic_ready, monkeypatch):
    """A worker exception must surface as some exception from watermarks().

    Doesn't pin the exception type — the refactor can re-raise as
    KafkaException, ExceptionGroup, the original RuntimeError, etc. —
    but silent return of empty watermarks must fail.
    """

    def boom(self, partition, **kwargs):
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(KafkaReport, "_get_lo_hi", boom)
    with pytest.raises(Exception):
        TARGET.watermarks(topic_ready)


def test_workers_run_in_threads(topic_ready, monkeypatch):
    """_get_lo_hi must be invoked once per partition on more than one thread.

    Catches serialization regressions (set collapses to 1) and missed-
    partition regressions (count != 3). Tolerant of thread-pool reuse.
    """
    seen_threads = []
    original = KafkaReport._get_lo_hi

    def spy(self, partition, **kwargs):
        seen_threads.append(threading.current_thread().ident)
        return original(self, partition, **kwargs)

    monkeypatch.setattr(KafkaReport, "_get_lo_hi", spy)

    TARGET.watermarks(topic_ready)

    assert len(seen_threads) == 3
    assert len(set(seen_threads)) > 1
