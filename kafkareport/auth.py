"""AWS MSK IAM auth wiring for both confluent_kafka and kafka-python.

Users opt into IAM by setting ``sasl.mechanism`` to the sentinel string
``AWS_MSK_IAM`` in their config. This module translates that into the real
``OAUTHBEARER`` plumbing each SDK wants — an ``oauth_cb`` callable for
confluent_kafka, an ``AbstractTokenProvider`` subclass for kafka-python —
both backed by ``aws-msk-iam-sasl-signer-python``.

Credentials come from the default boto3 chain. Region is resolved from
``boto3.Session().region_name`` (env, profile, IMDS) at config time so a
missing region fails fast rather than mid-handshake.
"""

from typing import Any

import boto3
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider
from kafka.sasl.oauth import AbstractTokenProvider

IAM_MECHANISM = "AWS_MSK_IAM"


def is_iam(conf: dict[str, Any]) -> bool:
    # `oauth_cb` is the post-injection marker — inject_confluent_iam overwrites
    # the sentinel `sasl.mechanism` with `OAUTHBEARER`, but only IAM sets oauth_cb.
    return conf.get("sasl.mechanism") == IAM_MECHANISM or "oauth_cb" in conf


def _resolve_region() -> str:
    region = boto3.Session().region_name
    if not region:
        raise RuntimeError(
            "AWS region not resolved for AWS_MSK_IAM auth. "
            "Set AWS_REGION or AWS_DEFAULT_REGION, or configure a "
            "default region via ~/.aws/config."
        )
    return region


def _build_oauth_cb(region: str, debug: bool):
    def oauth_cb(_oauth_config: str) -> tuple[str, float]:
        token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(region, aws_debug_creds=debug)
        # confluent_kafka expects expiry in seconds; signer returns ms.
        return token, expiry_ms / 1000

    return oauth_cb


class _MSKTokenProvider(AbstractTokenProvider):
    def __init__(self, region: str, debug: bool):
        self._region = region
        self._debug = debug

    def token(self) -> str:
        token, _ = MSKAuthTokenProvider.generate_auth_token(
            self._region, aws_debug_creds=self._debug
        )
        return token


def inject_confluent_iam(conf: dict[str, Any], debug: bool = False) -> None:
    """Mutate a confluent_kafka conf dict in place for IAM. No-op otherwise.

    Replaces the ``AWS_MSK_IAM`` sentinel with ``OAUTHBEARER``, forces
    ``SASL_SSL``, and adds the ``oauth_cb`` callback.
    """
    if not is_iam(conf):
        return
    region = _resolve_region()
    conf["sasl.mechanism"] = "OAUTHBEARER"
    conf["security.protocol"] = "SASL_SSL"
    conf["oauth_cb"] = _build_oauth_cb(region, debug)


def kafka_python_conf(conf: dict[str, Any], debug: bool = False) -> dict[str, Any]:
    """Translate a confluent_kafka conf dict into kafka-python kwargs.

    Handles plaintext (bootstrap only), SCRAM (4-key copy), and AWS_MSK_IAM
    (OAUTHBEARER + token provider).
    """
    kp_conf: dict[str, Any] = {"bootstrap_servers": conf["bootstrap.servers"]}
    if is_iam(conf):
        region = _resolve_region()
        kp_conf["security_protocol"] = "SASL_SSL"
        kp_conf["sasl_mechanism"] = "OAUTHBEARER"
        kp_conf["sasl_oauth_token_provider"] = _MSKTokenProvider(region, debug)
        # MSK Serverless drops pre-SASL connections, so kafka-python's
        # default ApiVersions probe loses every socket. Pin to skip it;
        # 2.0.0 is the floor that supports OAUTHBEARER (KIP-255).
        kp_conf["api_version"] = (2, 0, 0)
        return kp_conf
    try:
        kp_conf["sasl_plain_username"] = conf["sasl.username"]
        kp_conf["sasl_plain_password"] = conf["sasl.password"]
        kp_conf["sasl_mechanism"] = conf["sasl.mechanism"]
        kp_conf["security_protocol"] = "SASL_SSL"
    except KeyError:
        pass
    return kp_conf
