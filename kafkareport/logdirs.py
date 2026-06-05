# pylint: skip-file

import kafka
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.protocol.admin import DescribeLogDirsRequest


# kafka-python's native KafkaAdminClient.describe_log_dirs (since 2.1.0) takes no args
# and can't be targeted at a specific broker; per-broker support only landed on master
# (3.0.0.dev) via PR #2881. So we monkey-patch a per-broker variant that uses
# _send_request_to_node, reusing kafka-python's upstream protocol schema.
def describe_log_dirs(self, broker_id):
    """Send a DescribeLogDirsRequest to a specific broker."""
    version = self._client.api_version(DescribeLogDirsRequest, max_version=0)
    request = DescribeLogDirsRequest[version]()
    future = self._send_request_to_node(broker_id, request)
    self._wait_for_futures([future])
    return future.value


kafka.KafkaAdminClient.describe_log_dirs = describe_log_dirs


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
