# pylint: skip-file

import kafka
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.protocol.api import Request, Response

# isort: off
from kafka.protocol.types import (
    Array,
    Boolean,
    Bytes,
    Int8,
    Int16,
    Int32,
    Int64,
    Schema,
    String,
)

# isort: on


# Neither kafka-python nor confluent-kafka-python have implemented the DescribeLogDirsRequest
# WIP PR on kafka-python: see https://github.com/dpkp/kafka-python/pull/2278
# Backported stuff from the PR to support DescribeLogDirs with kafka-python
class DescribeLogDirsResponse_v0(Response):
    API_KEY = 35
    API_VERSION = 0
    FLEXIBLE_VERSION = True
    SCHEMA = Schema(
        ("throttle_time_ms", Int32),
        (
            "log_dirs",
            Array(
                ("error_code", Int16),
                ("log_dir", String("utf-8")),
                (
                    "topics",
                    Array(
                        ("name", String("utf-8")),
                        (
                            "partitions",
                            Array(
                                ("partition_index", Int32),
                                ("partition_size", Int64),
                                ("offset_lag", Int64),
                                ("is_future_key", Boolean),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


class DescribeLogDirsRequest_v0(Request):
    API_KEY = 35
    API_VERSION = 0
    RESPONSE_TYPE = DescribeLogDirsResponse_v0
    SCHEMA = Schema(
        ("topics", Array(("topic", String("utf-8")), ("partitions", Int32)))
    )


DescribeLogDirsResponse = [
    DescribeLogDirsResponse_v0,
]
DescribeLogDirsRequest = [
    DescribeLogDirsRequest_v0,
]


# Note we add a custom broker_id to avoid fetching from the current broker
def describe_log_dirs(self, broker_id):
    """Send a DescribeLogDirsRequest request to a broker.
    :return: A message future
    """
    version = self._matching_api_version(DescribeLogDirsRequest)
    if version <= 1:
        request = DescribeLogDirsRequest[version]()
        future = self._send_request_to_node(broker_id, request)
        self._wait_for_futures([future])
    else:
        raise NotImplementedError(
            "Support for DescribeLogDirsRequest_v{} has not yet been added to KafkaAdminClient.".format(
                version
            )
        )
    return future.value


# monkey patch admin client
kafka.KafkaAdminClient.describe_log_dirs = describe_log_dirs
##############################################################################


# Our little helpers to gather the size of all partitions across all brokers
def get_log_dir_size(clientAdmin):
    """
    Return a dict of broker id -> topic -> partition -> size
    Goal: gather all data we might need one day or another.
    """
    brokersIds = [data["node_id"] for data in clientAdmin.describe_cluster()["brokers"]]
    logDirSizes = {}  # broker id -> topic -> partition -> size
    for brokerId in brokersIds:
        logDirSizes[brokerId] = {}
        logDirResponse = clientAdmin.describe_log_dirs(brokerId)
        for topic, partitions in logDirResponse.log_dirs[0][2]:
            logDirSizes[brokerId][topic] = {}
            logDirSizes[brokerId][topic] = {}
            for partition in partitions:
                logDirSizes[brokerId][topic][partition[0]] = partition[1]
    return logDirSizes


# Example of usage: sum topic size per broker id
def gen_size_metrics(logDirSizes):
    """
    Compute Sum of partition size per topic per broker
    Return a list of metrics to be sent to datadog
    each metric is a dict, ({"value": <value as float>, "tags": <tags as list of strings>})
    Eeach tag is a string of the form "key:value"
    """
    metrics = []
    for brokerId in logDirSizes.keys():
        for topic in logDirSizes[brokerId]:
            size = sum(logDirSizes[brokerId][topic].values())
            tags = [f"broker_id:{brokerId}", f"topic:{topic}"]
            metrics.append({"value": size, "tags": tags})
    return metrics


def doit(conf):
    """Gets all LogDir sizes for all topics."""
    kp_conf = {"bootstrap_servers": conf["bootstrap.servers"]}
    try:
        kp_conf["sasl_plain_username"] = conf["sasl.username"]
        kp_conf["sasl_plain_password"] = conf["sasl.password"]
        kp_conf["sasl_mechanism"] = conf["sasl.mechanism"]
        kp_conf["security_protocol"] = "SASL_SSL"
    except KeyError:
        pass
    admin = KafkaAdminClient(**kp_conf)
    return get_log_dir_size(admin)
