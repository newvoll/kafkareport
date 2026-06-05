# Live test against real MSK

A throwaway MSK cluster + bastion for verifying `kafkareport` end-to-end before
publishing. Two flavors, picked by which CFN template you deploy:

| Mode | Template | Auth | Cost (approx) |
| --- | --- | --- | --- |
| Provisioned | `msk.yaml` | SASL/SCRAM | **~$280+/month** at default size (`kafka.m7g.large` × 2 + storage) |
| Serverless | `msk-iam.yaml` | IAM (`AWS_MSK_IAM`) | Per-partition-hour + traffic, no broker fee. Cheaper than provisioned, **not free** — an idle cluster still bills. |

**Tear it down when you're done either way.**

> [!WARNING]
> LLM-created for throwaway smoke tests. Use only on nonproduction accounts.

## Deploy

### Provisioned (SCRAM)

```sh
aws cloudformation deploy \
  --stack-name kafkareport-livetest \
  --template-file livetest/msk.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      KafkaUsername=kafkareport \
      KafkaPassword='<pick a 12+ char password>'
```

MSK provisioning takes 15–25 minutes.

### Serverless (IAM)

```sh
aws cloudformation deploy \
  --stack-name kafkareport-livetest-iam \
  --template-file livetest/msk-iam.yaml \
  --capabilities CAPABILITY_IAM
```

Serverless provisioning is usually faster (a few minutes).

## Grab the outputs

```sh
aws cloudformation describe-stacks --stack-name <your-stack-name> \
  --query 'Stacks[0].Outputs' --output table
```

You'll get a `ClusterArn` and a `BastionInstanceId` from either stack.

## Build the conf file

### Provisioned (SCRAM)

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

### Serverless (IAM)

Get the IAM bootstrap broker string:

```sh
aws kafka get-bootstrap-brokers --cluster-arn <ClusterArn> \
  --query BootstrapBrokerStringSaslIam --output text
```

Make a `conf.json` — note the `AWS_MSK_IAM` sentinel; no user/pass:

```json
{
  "bootstrap.servers": "<the BootstrapBrokerStringSaslIam value>",
  "sasl.mechanism": "AWS_MSK_IAM"
}
```

Credentials come from the default AWS chain. On the bastion that's the
instance profile (which the IAM stack grants `kafka-cluster:*` on the
cluster's topics and groups). Region is exported into the bastion's
shell at launch by user-data, so `AWS_REGION` is already set when you
SSM in.

## Run from the bastion

The MSK brokers / serverless endpoints live in private subnets. Easiest way in
is the bastion the stack provisioned, via SSM (no SSH key needed):

```sh
aws ssm start-session --target <BastionInstanceId>
```

On the bastion (user-data installs `git`, `python3.14`, and `pip3.14` at
launch — if the first command fails, `cloud-init status --wait` and retry):

```sh
git clone https://github.com/newvoll/kafkareport
cd kafkareport
pip3.14 install --user .
# copy conf.json onto the box however you like (paste into nano, scp via SSM, etc.)
python3.14 livetest/populate.py conf.json
kafkareport conf.json
```

The populate script creates three topics (`kafkareport-small`, `-medium`,
`-large`) with varied partition counts, retentions, and message sizes so the
reports show meaningful differences. It runs the same way against either
auth mode — the `AWS_MSK_IAM` sentinel is translated by `kafkareport.auth`
before the producer/admin client are built.

## Teardown

```sh
aws cloudformation delete-stack --stack-name <your-stack-name>
```

This deletes the cluster, bastion, VPC, and (for SCRAM) the KMS key and
secret. The KMS key is scheduled for deletion (7 day default window); the
rest goes immediately.
