"""Reports on topic sizes, watermarks, and retention policies."""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Union

from confluent_kafka import (
    Consumer,
    KafkaError,
    KafkaException,
    Message,
    TopicPartition,
)
from confluent_kafka.admin import RESOURCE_TOPIC, AdminClient, ConfigResource

from kafkareport import helpers, logdirs

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(levelname)-.1s %(message)s <%(module)s.%(funcName)s>",
    stream=sys.stdout,
)


class KafkaReport:
    """The only class. _TIMEOUT classvar is default unless passed via kwargs to the methods.

      :param conf: A dict passed to confluent_kafka's consumer, so it should follow the `same format <https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#pythonclient-configuration>`_, except for group.id. It is automatically set.

      :param debug: sets the logger level to DEBUG

    Typical usage:

    from kafkareport import KafkaReport

    conf = {"bootstrap.servers": "localhost:9092", "ssl.endpoint.identification.algorithm": "none"}

    report = KafkaReport(conf)

    report.get_topicnames()

    for topic in report.get_topicnames():

         report.retentions(topic)

         report.watermarks(topic)

    report.topic_sizes()

    report.watermarks("kafkareportuno")

    """  # pylint: disable=line-too-long

    _TIMEOUT = 30

    def __init__(
        self, conf: dict[str, Union[str, bool, Callable]], debug: bool = False
    ):
        self.debug = debug
        if self.debug:
            logger.setLevel(logging.DEBUG)
        self.conf = conf
        self.name = "kafkareport"
        self.admin = AdminClient(self.conf)
        self.consumer_conf = self.conf.copy()
        self.consumer_conf["enable.auto.commit"] = False
        self.consumer_conf["enable.partition.eof"] = True
        self.consumer_conf["group.id"] = f"{self.name}-{time.time()}"

    def __repr__(self):
        return self.__class__.__name__

    def _error_cb(self, error: KafkaError) -> None:
        """Callback for watermark thread errors to catch potential issues.

        Raising the error once lead to a traceback formatting error
        during local testing but then magically started working again.
        """
        logger.fatal(error)
        raise error

    def _get_offset_message(
        self,
        consumer: Consumer,
        partition: TopicPartition,
        offset: int,
        timeout: int = _TIMEOUT,
    ) -> Message:
        """Returns message at partition offset (hi or lo).

        :param consumer: Previously configured consumer client.
        :param partition: Partition for which to find message.
        :param offset: Beginning or end.
        :param timeout: Seconds to wait for timeout.
        :return: Message at the mark.
        """
        partition.offset = offset
        consumer.assign([partition])
        logger.debug("getting message %s", consumer)
        messages = consumer.consume(num_messages=1, timeout=timeout)
        try:
            message = messages[0]
        except IndexError as e:
            logger.critical("Timed out.")
            raise e
        logger.debug("message %s got.", message)
        if message.error():
            if message.error().code() == -191:
                logger.debug("_PARTITION_EOF. Nothing to see here")
            else:
                logger.fatal("Message consume error: %s", message.error())
                raise message.error()
        return message

    def _get_lo_hi(
        self,
        partition: TopicPartition,
        result: list[tuple],
        index: int,
        timeout: int = _TIMEOUT,
    ) -> None:
        """Threaded partition watermarks. Modifies result[i] rather than return.

        :param partition: Partition for which to find message.
        :param result: list of tuples to update with hi, lo marks.
        :param index: index of the result list to update.
        :param timeout: Seconds to wait for timeout.
        """
        conf = self.consumer_conf.copy()
        conf["error_cb"] = self._error_cb
        conf["group.id"] = f"{self.name}-{time.time()}-watermark-thread"
        consumer = Consumer(conf, logger=logger)
        (lo, hi) = consumer.get_watermark_offsets(
            partition, timeout=timeout, cached=False
        )
        message = self._get_offset_message(consumer, partition, lo, timeout=timeout)
        minnie = message.timestamp()[1]
        earliest = message
        message = self._get_offset_message(consumer, partition, hi - 1, timeout=timeout)
        maxie = message.timestamp()[1]
        latest = message
        consumer.close()
        logger.debug("%s: %s-%s", partition, minnie, maxie)
        result[index] = (earliest, latest)

    def retentions(self, topic: str, timeout: int = _TIMEOUT) -> dict[str, int]:
        """Retrieves the retention settings for given topic.

        :param topic: A string of the topic name.
        :param timeout: Seconds to wait for timeout.
        :return: A dict of the topic's retention settings and values.
        """
        consumer = Consumer(self.consumer_conf, logger=logger)
        metadata = consumer.list_topics(topic, timeout=timeout)
        if metadata.topics[topic].error is not None:
            raise KafkaException(metadata.topics[topic].error)
        res = self.admin.describe_configs([ConfigResource(RESOURCE_TOPIC, topic)])
        for _, f in res.items():
            retentions = [str(v) for k, v in f.result().items() if "retention" in k]
        result = {}
        for ret in retentions:
            splitz = ret.split("=")
            value = splitz[1].replace('"', "")
            result[str(splitz[0])] = int(value)
        consumer.close()
        return result

    def _watermark_results(
        self, results: list[tuple[Message, Message]], topic: str
    ) -> tuple:
        """Processes partition thread results for earliest and latest.

        Also prints out message contents with logLevel == DEBUG.

        :param results: A list of results fro _get_lo_hi
        :returns: { "earliest": datetime, "latest": datetime }
        """
        earliests = [x[0] for x in results if not x[0].error()]
        latests = [x[1] for x in results if not x[1].error()]
        earliests.sort(key=lambda x: x.timestamp()[1])
        latests.sort(key=lambda x: x.timestamp()[1], reverse=True)
        try:
            earliest_message = min(earliests, key=lambda x: x.timestamp()[1])
            earliest = earliest_message.timestamp()[1]
            early = datetime.fromtimestamp(earliest / 1000).astimezone(timezone.utc)
            earliest_val = earliest_message.value().decode("utf-8")
            logger.debug("Earliest message %s: %s", early, earliest_val)
        except ValueError:
            logger.debug("No earliest watermarks for %s.", topic)
            early = ""
        try:
            latest_message = max(latests, key=lambda x: x.timestamp()[1])
            latest = latest_message.timestamp()[1]
            late = datetime.fromtimestamp(latest / 1000).astimezone(timezone.utc)
            latest_val = latest_message.value().decode("utf-8")
            logger.debug("Latest message %s: %s", late, latest_val)
        except ValueError:
            logger.debug("No latest watermark for %s.", topic)
            late = ""
        return (early, late)

    def watermarks(self, topic: str, timeout: int = _TIMEOUT) -> dict[str, datetime]:
        """Retrieves the earliest and latest message times for each topic.

        Spans all partitions. Launches a thread for each
        partition. UTC times. Empty strings if no result.

        :param topic: A string of the topic name.
        :param timeout: Seconds to wait for timeout.
        :raises KafkaException: If there was a problem getting topic metadata.
        :return: A dict of the topic's earliest and latest message times.
        """
        consumer = Consumer(self.consumer_conf, logger=logger)
        metadata = consumer.list_topics(topic, timeout=timeout)
        if metadata.topics[topic].error is not None:
            raise KafkaException(metadata.topics[topic].error)
        partitions = [
            TopicPartition(topic, p) for p in metadata.topics[topic].partitions
        ]
        committed = consumer.committed(partitions, timeout=timeout)
        threads = []
        results = [(None, None)] * len(committed)
        logger.debug("%s threads launching for %s", len(committed), topic)
        for i, partition in enumerate(committed):
            logger.debug(partition)
            thread = threading.Thread(
                target=self._get_lo_hi,
                args=(partition, results, i),
                kwargs={"timeout": timeout},
            )
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        (early, late) = self._watermark_results(results, topic)
        consumer.close()
        return {"earliest": early, "latest": late}

    def get_topicnames(self, timeout: int = _TIMEOUT) -> list[str]:
        """Returns a list of all topic names from the kafka servers.

        :return: A list of all topic names
        """
        return self.admin.list_topics(timeout=timeout).topics.keys()

    def topic_sizes(self) -> list[dict[str, Union[str, int]]]:
        """Retrieves the size each topic takes up on the servers.

        At time of first writing, neither kafka-python nor
        confluent_kafka implemented the DescribeLogDirsRequest,
        available in java. A recent PR for kafka-python supports it,
        so I used that experimental code. Not pretty, but it works.

        :param timeout: Seconds to wait for timeout.
        :return: A dict of the topic's names and sizes in bytes,
        """
        sizes = logdirs.doit(self.conf)
        size_by_name = {}
        for topics in sizes.values():
            for topic, partitions in topics.items():
                for partition, size in partitions.items():
                    logger.debug("%s, %s", topic, partition)
                    try:
                        size_by_name[topic] = round(size_by_name[topic] + size)
                    except KeyError:
                        size_by_name[topic] = round(size)
        sorted_by_size = sorted(size_by_name.items(), key=lambda x: x[1], reverse=True)
        result = []
        for topic in sorted_by_size:
            size = round(int(topic[1]))
            res = {
                "topic": topic[0],
                "bytes": size,
            }
            result.append(res)
        return result
