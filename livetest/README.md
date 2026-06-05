# Live test against real MSK

A throwaway MSK cluster + bastion for verifying `kafkareport` end-to-end before
publishing. Provisioned MSK with SASL/SCRAM, since that's what `logdirs.py`
currently supports.

**Costs roughly $280+/month at the default size (`kafka.m7g.large` × 2
brokers + storage). Tear it down when you're done.**

## Deploy

```sh
aws cloudformation deploy \
  --stack-name kafkareport-livetest \
  --template-file livetest/msk.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      KafkaUsername=kafkareport \
      KafkaPassword='<pick a 12+ char password>'
```

MSK provisioning takes 15–25 minutes. Grab the outputs once it's done:

```sh
aws cloudformation describe-stacks --stack-name kafkareport-livetest \
  --query 'Stacks[0].Outputs' --output table
```

You'll get a `ClusterArn` and a `BastionInstanceId`.

## Build the conf file

Get the SASL/SCRAM bootstrap broker string:

```sh
aws kafka get-bootstrap-brokers --cluster-arn <ClusterArn> \
  --query BootstrapBrokerStringSaslScram --output text
```

Make a `conf.json`:

```json
{
  "bootstrap.servers": "<the BootstrapBrokerStringSaslScram value>",
  "security.protocol": "SASL_SSL",
  "sasl.mechanism": "SCRAM-SHA-512",
  "sasl.username": "kafkareport",
  "sasl.password": "<the password you set>"
}
```

## Run from the bastion

The MSK brokers live in private subnets. Easiest way in is the bastion the
stack provisioned, via SSM (no SSH key needed):

```sh
aws ssm start-session --target <BastionInstanceId>
```

On the bastion:

```sh
sudo dnf install -y git
git clone https://github.com/newvoll/kafkareport
cd kafkareport
pip install --user .
# copy conf.json onto the box however you like (paste into nano, scp via SSM, etc.)
python livetest/populate.py conf.json
kafkareport conf.json
```

The populate script creates three topics (`kafkareport-small`, `-medium`,
`-large`) with varied partition counts, retentions, and message sizes so the
reports show meaningful differences.

## Teardown

```sh
aws cloudformation delete-stack --stack-name kafkareport-livetest
```

This deletes the cluster, bastion, VPC, KMS key, and secret. The KMS key is
scheduled for deletion (7 day default window); the rest goes immediately.
