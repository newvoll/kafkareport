"""Covers all the lib's functionality."""

import json
import logging
import sys
import time
from datetime import datetime, timezone

import pytest
from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewPartitions, NewTopic

from kafkareport import KafkaReport

logger = logging.getLogger(__name__)
ADMIN = AdminClient(pytest.conf)
TARGET = KafkaReport(pytest.conf)


class TestTopics:
    """Holds all the tests."""

    @pytest.fixture(autouse=True)
    def _init_topics(self):
        """Creates pytest.topics with 3 paritions each before each
        test in the class.
        """
        self.create_topics()
        self.add_partitions()

    def add_partitions(self):
        """Default seems to be 3, but just in case."""
        new_partitions = []
        for topic in pytest.topics:
            new_partitions.append(NewPartitions(topic, 3))
        fs = ADMIN.create_partitions(new_partitions)
        start = time.time()
        timeout = 30
        for _, f in fs.items():
            logger.info("Waiting for %s to finish partitioning...", _)
            while f.running() is True:
                if time.time() > start + timeout:
                    logger.critical("Partitioning timed out.")
                    sys.exit(1)
                time.sleep(1)
            try:
                f.result()
                logger.info("Topic %s partitioned.", _)
            except KafkaException as e:
                if e.args[0].code() == 37 and "already has" in e.args[0].str():
                    pass
                else:
                    logger.critical("Failed to partition topic %s: %s", _, e)
                    raise e

    def create_topics(self):
        """Creates pytest.topics."""
        new_topics = []
        for i, topicname in enumerate(pytest.topics):
            logger.debug(i)
            config = {
                "delete.retention.ms": "16800001",
                "retention.ms": "16800001",
                "retention.bytes": "16800001",
            }
            new_topics.append(
                NewTopic(
                    topicname, num_partitions=3, replication_factor=1, config=config
                )
            )
        # Note: In a multi-cluster production scenario, it is more
        # typical to use a replication_factor of 3 for durability.
        logger.info("creating topics %s", pytest.topics)
        fs = ADMIN.create_topics(new_topics, request_timeout=1)
        # Wait for each operation to finish.
        start = time.time()
        timeout = 30
        for _, f in fs.items():
            logger.info("Waiting for %s to finish creating...", _)
            while f.running() is True:
                if time.time() > start + timeout:
                    logger.critical("Topic creation timed out.")
                    sys.exit(1)
                time.sleep(1)
            try:
                f.result()
                logger.info("Topic %s created", _)
            except KafkaException as e:
                logger.critical("Failed to create topic %s: %s", _, e)
                raise e

    def test_empty_topics(self):
        """Makes sure the report can handle empty. Good smoke test."""
        for topic in pytest.topics:
            assert topic in TARGET.get_topicnames()
        topics = [x for x in TARGET.topic_sizes() if x["topic"] in pytest.topics]
        logger.info(topics)
        assert sorted([x["topic"] for x in topics]) == sorted(pytest.topics)
        for topic in topics:
            assert topic["bytes"] == 0
            watermarks = TARGET.watermarks(topic["topic"])
            assert watermarks["earliest"] == ""
            assert watermarks["latest"] == ""
            retentions = TARGET.retentions(topic["topic"])
            assert retentions["retention.ms"] == 16800001
            assert retentions["retention.bytes"] == 16800001
            assert retentions["delete.retention.ms"] == 16800001

    @pytest.mark.parametrize("messages_to_produce", [1, 30])
    def test_topics(self, messages_to_produce):
        """First adds one message, ensuring that partition EOFs are
        handled, then 30 to fill out all the partitions.
        """
        before, after = self.populate_topics(pytest.topics, messages_to_produce)
        topics = [x for x in TARGET.topic_sizes() if x["topic"] in pytest.topics]
        topicnames = [x["topic"] for x in topics]
        assert sorted(topicnames) == sorted(pytest.topics)
        for topic in topics:
            if messages_to_produce == 1:
                assert topic["bytes"] == 89
            else:
                assert (
                    messages_to_produce * 0.98
                    <= topic["bytes"]
                    >= messages_to_produce * 1.02
                )
            watermarks = TARGET.watermarks(topic["topic"])
            assert watermarks["earliest"] <= after
            assert watermarks["latest"] <= after
            assert watermarks["earliest"] >= before
            assert watermarks["latest"] >= before
            assert watermarks["earliest"] <= watermarks["latest"]
            retentions = TARGET.retentions(topic["topic"])
            assert retentions["retention.ms"] == 16800001
            assert retentions["retention.bytes"] == 16800001
            assert retentions["delete.retention.ms"] == 16800001

    def populate_topics(self, topics, n):
        """Produces some messages for the topics, sleeping a tiny bit
        to avoid timing weirdness.
        """
        timetosleep = 0.05
        before_populate = datetime.now(tz=timezone.utc)
        logger.info("Producing %s messages", n)
        time.sleep(timetosleep)
        producer = Producer(pytest.conf)
        for topic in topics:
            for i in range(n):
                message = {topic: i}
                logger.debug("producing %s %s", topic, i)
                producer.produce(topic=topic, value=json.dumps(message))
                time.sleep(timetosleep)
        producer.flush()
        time.sleep(timetosleep)
        after_populate = datetime.now(tz=timezone.utc)
        return before_populate, after_populate
