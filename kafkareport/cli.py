"""CLI for the KafkaReport lib."""

#!/usr/bin/env python3

import argparse
import csv
import json
import logging
import pprint
import sys
from importlib.metadata import metadata, version
from textwrap import wrap

import confluent_kafka
import kafka
from prettytable import PrettyTable

from kafkareport import KafkaReport, helpers

logger = logging.getLogger(__name__)
pp = pprint.PrettyPrinter(indent=4)
parser = argparse.ArgumentParser()
parser.add_argument("conf_file", help="file with confluent consumer conf (json)")
parser.add_argument(
    "-t",
    "--topics",
    default="",
    nargs="+",
    help="optional topics list, e.g. -t uno due.",
)
parser.add_argument(
    "-s",
    "--sizes",
    action="store_true",
    default=False,
    help="show logdir size for each topic across all brokers/partitions.",
)
parser.add_argument(
    "-p",
    "--show-private",
    action="store_true",
    default=False,
    help="include topics starting with '_'.",
)
parser.add_argument(
    "-f",
    "--full",
    action="store_true",
    default=True,
    help="show everything (watermarks, retentions) along with sizes.",
)
parser.add_argument(
    "-w",
    "--watermarks",
    action="store_true",
    default=False,
    help="show times of earliest and latest messages across all partitions of topic.",
)
parser.add_argument(
    "-r",
    "--retentions",
    action="store_true",
    default=False,
    help="show retention configs for topics.",
)
parser.add_argument(
    "--csv", action="store", default=None, help="filename for csv output of table"
)
parser.add_argument("-v", "--verbose", action="store_true", default=False, help="DEBUG")
parser.add_argument(
    "--version",
    action="version",
    version=f"{metadata('kafkareport')['Name']} {version('kafkareport')}",
    help="print the version and quit",
)
args = parser.parse_args()


def _limit_string(val, limit=40):
    return "\n".join(wrap(str(val), limit))


def _print_outs(outs):
    """Attempts to make a table from a list of dicts, filling out keys
    that don't exist. pprint if it fails.
    """
    try:
        keys = set().union(*outs)
        for out in outs:
            for key in keys - out.keys():
                out[key] = ""
        table = PrettyTable()
        headings = list(outs[0])
        table.field_names = headings
        table.align = "l"
        for out in outs:
            if out:
                table.add_row([_limit_string(out[x]) for x in headings])
        if len(table.rows) > 0:
            print(table)
    except (TypeError, AttributeError):  # pprint if table trouble
        pp.pprint(outs)
    except (KeyError, IndexError):  # for none
        pass


def _write_csv(outs):
    with open(args.csv, "w", encoding="UTF8") as f:
        writer = csv.writer(f)
        writer.writerow(outs[0].keys())
        for out in outs:
            writer.writerow([out[x] for x in outs[0].keys()])


def _get_conf():
    conf_json = helpers.slurp(args.conf_file)
    return json.loads(conf_json)


def _doit():
    """Create the report."""
    report = KafkaReport(_get_conf(), debug=args.verbose)
    if args.watermarks or args.retentions or args.sizes:
        args.full = False
    if args.sizes or args.full:
        topics = report.topic_sizes()
    else:
        topics = [{"topic": x} for x in report.get_topicnames()]
    if args.topics:
        topics = [x for x in topics if x["topic"] in args.topics]
    if not args.show_private:
        topics = [x for x in topics if not x["topic"].startswith("_")]
    for topic in topics:
        if args.watermarks or args.full:
            watermarks = report.watermarks(topic["topic"])
            topic["earliest"] = watermarks["earliest"]
            topic["latest"] = watermarks["latest"]
        if args.retentions or args.full:
            retentions = report.retentions(topic["topic"])
            topic.update(retentions)
    return topics


def main():
    """Handle errors and print results."""
    try:
        result = _doit()
    except KeyboardInterrupt:
        logger.fatal("Interrupted")
        sys.exit(1)
    except (confluent_kafka.KafkaError, confluent_kafka.KafkaException) as e:
        logger.fatal("confluent_kafka error: %s", e)
        sys.exit(1)
    except kafka.errors.KafkaError as e:
        logger.fatal("kafka-python error: %s", e)
        sys.exit(1)

    if not result:
        print("No result (auth error may be misleading).")
        sys.exit(0)
    else:
        _print_outs(result)
        if args.csv:
            _write_csv(result)
