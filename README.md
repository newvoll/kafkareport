A reporting tool for kafka topics.

Show any or all of the following attributes for each topic in a cluster:
* sizes (`DescribeLogDirsRequest`)
* retention policies
* earliest/latest message timestamps (watermarks)

With larger teams and installations, misconfiguration often results in
lots of resource waste. Auditing with this tool has helped.

# Installation

`pip install kafkareport`

# Usage

All times should be UTC.

## Configuration
Json file of a dict passed to confluent_kafka's consumer, so it should follow the
[same
format](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#pythonclient-configuration)
except for `group.id`. It is automatically set. See
[tests/helper_files/env.json](https://github.com/newvoll/kafkareport/blob/main/tests/helper_files/env.json)
for a sample conf file.

> [!NOTE]
> Tested only with `SASL_SSL` + `SCRAM_SHA_512` u/p on AWS MSK
> provisioned, and `AWS_MSK_IAM` on MSK Serverless. See
> [Addenda](#Addenda) for details.

### AWS MSK IAM

Set `sasl.mechanism` to the sentinel `AWS_MSK_IAM` and drop the
`sasl.username` / `sasl.password` keys:

```json
{
    "bootstrap.servers": "boot-xxxx.kafka-serverless.us-east-1.amazonaws.com:9098",
    "sasl.mechanism": "AWS_MSK_IAM"
}
```

Credentials come from the default AWS chain (env, profile, instance
profile). Region resolves from `AWS_REGION` / `AWS_DEFAULT_REGION` /
`~/.aws/config`. `KafkaReport(debug=True)` also flips the signer's
credential-debug, which logs the IAM principal actually in use — handy
when access is denied.


## CLI

 `-h` for help.

```
% kafkareport ~/aws.json --csv report.csv
+----------------+-------+----------------------------------+----------------------------------+--------------+-----------------+---------------------+
| topic          | bytes | earliest                         | latest                           | retention.ms | retention.bytes | delete.retention.ms |
+----------------+-------+----------------------------------+----------------------------------+--------------+-----------------+---------------------+
| kafkareportdue | 2330  | 2024-06-30 20:04:44.486000+00:00 | 2024-06-30 20:04:46.041000+00:00 | 16800001     | 16800001        | 16800001            |
| kafkareportuno | 2268  | 2024-06-30 20:04:42.880000+00:00 | 2024-06-30 20:04:44.431000+00:00 | 16800001     | 16800001        | 16800001            |
+----------------+-------+----------------------------------+----------------------------------+--------------+-----------------+---------------------+

% cat report.csv
topic,bytes,earliest,latest,retention.ms,retention.bytes,delete.retention.ms
kafkareportdue,2330,2024-06-30 20:04:44.486000+00:00,2024-06-30 20:04:46.041000+00:00,16800001,16800001,16800001
kafkareportuno,2268,2024-06-30 20:04:42.880000+00:00,2024-06-30 20:04:44.431000+00:00,16800001,16800001,16800001
```

## Lib

[Docs](https://kafkareport.readthedocs.io/en/latest/kafkareport.html#module-kafkareport)

```
>>> from kafkareport import KafkaReport

>>> conf = {"bootstrap.servers": "localhost:9092", "ssl.endpoint.identification.algorithm": "none"}

>>> report = KafkaReport(conf)

>>> report.get_topicnames()
dict_keys(['kafkareportdue', 'kafkareportuno', '__consumer_offsets'])

>>> for topic in report.get_topicnames():
...     print(topic)
...     report.retentions(topic)
...     report.watermarks(topic)
...
kafkareportdue
{'retention.ms': 16800001, 'retention.bytes': 16800001, 'delete.retention.ms': 16800001}
{'earliest': datetime.datetime(2024, 6, 30, 20, 8, 57, 554000, tzinfo=datetime.timezone.utc), 'latest': datetime.datetime(2024, 6, 30, 20, 8, 59, 99000, tzinfo=datetime.timezone.utc)}
kafkareportuno
{'retention.ms': 16800001, 'retention.bytes': 16800001, 'delete.retention.ms': 16800001}
{'earliest': datetime.datetime(2024, 6, 30, 20, 8, 55, 975000, tzinfo=datetime.timezone.utc), 'latest': datetime.datetime(2024, 6, 30, 20, 8, 57, 503000, tzinfo=datetime.timezone.utc)}
__consumer_offsets
{'retention.ms': 604800000, 'retention.bytes': -1, 'delete.retention.ms': 86400000}
{'earliest': '', 'latest': ''}

>>> report.topic_sizes()
[{'topic': 'kafkareportuno', 'bytes': 2690}, {'topic': 'kafkareportdue', 'bytes': 2328}, {'topic': '__consumer_offsets', 'bytes': 0}]

>>> report.watermarks("kafkareportuno")
{'earliest': datetime.datetime(2024, 6, 30, 20, 8, 55, 975000, tzinfo=datetime.timezone.utc), 'latest': datetime.datetime(2024, 6, 30, 20, 8, 57, 503000, tzinfo=datetime.timezone.utc)}
```

# Development
This is a [poetry](https://python-poetry.org/) project, so it should
be butter once you get that sorted. Install
[pre-commit](https://pre-commit.com/) for black on commit, lint on
push. Couldn't figure `pytype` into `pre-commit`.

`pre-commit install --hook-type pre-push` for lint pre-push.

# Testing
Testing runs against kafka/zookeper containers, as you can see in the
[Github
actions](https://github.com/newvoll/kafkareport/actions). `docker-compose
up` should do the trick.

`pytest` can use `--conf=/some/file.json` instead of default for
localstack. This will manipulate pytest.topics on the kafka servers.

## Testing Gotchas
* Occasionally, e.g. on laptop wake, the kafka container will be in a
  weird state. `docker-compose down --remove-orphans && docker-compose
  up` always did the trick.

# Addenda
* Watermarks use a thread for each topic partition, but it can still take a while. `-v` for gory details along the way, `KafkaReport(debug=True)` for the lib.
* This project started with
  [confluent_kafka](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#),
  but `DescribeLogDirsRequest` (available in Java) still isn't
  implemented in
  [confluent_kafka](https://github.com/confluentinc/confluent-kafka-python/issues/1179)
  or the underlying
  [librdkafka](https://github.com/confluentinc/librdkafka/issues/5333).
  [kafka-python](https://kafka-python.readthedocs.io/en/master/) has
  had a native `describe_log_dirs` since 2.1.0, but the stable 2.x
  signature can't target a specific broker — that landed on
  master/3.0.0.dev via [PR
  #2881](https://github.com/dpkp/kafka-python/pull/2881). Until that
  ships, `logdirs.py` monkey-patches a per-broker wrapper onto
  `KafkaAdminClient.describe_log_dirs`.
  - As a result, there is a janky confluent_kafka to kafka-python
    AdminClient conf map, now lifted into `kafkareport/auth.py` since
    IAM doubled its complexity. It handles plaintext, SCRAM, and the
    `AWS_MSK_IAM` sentinel (translated to `OAUTHBEARER` + an MSK
    signer-backed token provider for kafka-python and an `oauth_cb`
    for confluent_kafka). Tested manually against MSK provisioned
    (SCRAM) and MSK Serverless (IAM); CI runs against plaintext docker
    only.
