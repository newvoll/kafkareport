"""Populate the live-test MSK cluster with topics and varied test data.

Creates three topics with intentionally different partition counts, retention
configs, and message sizes so kafkareport's reports show meaningful variation.
"""

import argparse
import json
import logging
import random
import string
import sys
import time
from pathlib import Path

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

from kafkareport.auth import inject_confluent_iam

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-.1s %(message)s",
    stream=sys.stdout,
)

TOPICS = [
    # name, partitions, messages, payload_bytes, retention_ms, retention_bytes
    ("kafkareport-small", 3, 500, 50, 3_600_000, 100_000_000),
    ("kafkareport-medium", 6, 1000, 200, 86_400_000, 500_000_000),
    ("kafkareport-large", 12, 1500, 500, 604_800_000, 2_000_000_000),
]


def make_admin(conf):
    return AdminClient(conf)


def create_topics(admin):
    new_topics = [
        NewTopic(
            name,
            num_partitions=partitions,
            replication_factor=2,
            config={
                "retention.ms": str(retention_ms),
                "retention.bytes": str(retention_bytes),
                "delete.retention.ms": str(min(retention_ms, 86_400_000)),
            },
        )
        for name, partitions, _, _, retention_ms, retention_bytes in TOPICS
    ]
    logger.info("creating topics: %s", [t.topic for t in new_topics])
    futures = admin.create_topics(new_topics, request_timeout=10)
    deadline = time.time() + 60
    for name, future in futures.items():
        while future.running():
            if time.time() > deadline:
                sys.exit(f"timed out creating {name}")
            time.sleep(1)
        try:
            future.result()
            logger.info("created %s", name)
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.warning("%s already exists, continuing", name)
            else:
                raise


def populate(producer, name, count, payload_bytes):
    """Produce `count` messages of roughly `payload_bytes` each."""
    padding_len = max(0, payload_bytes - 40)
    sent = 0
    for i in range(count):
        padding = "".join(random.choices(string.ascii_letters + string.digits, k=padding_len))
        message = {"i": i, "ts": time.time(), "pad": padding}
        producer.produce(topic=name, value=json.dumps(message).encode("utf-8"))
        sent += 1
        if sent % 500 == 0:
            producer.poll(0)
            logger.info("  %s: %d/%d", name, sent, count)
    producer.flush(timeout=60)
    logger.info("%s: %d messages produced", name, count)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("conf_file", help="JSON conf file (same shape as kafkareport's)")
    args = parser.parse_args()

    conf = json.loads(Path(args.conf_file).read_text(encoding="utf-8"))
    inject_confluent_iam(conf)

    admin = make_admin(conf)
    create_topics(admin)

    producer = Producer(conf)
    for name, _, count, payload_bytes, _, _ in TOPICS:
        populate(producer, name, count, payload_bytes)

    logger.info("done. run `kafkareport %s` to see the report.", args.conf_file)


if __name__ == "__main__":
    main()
