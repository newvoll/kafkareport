# Live test against real MSK

A throwaway MSK provisioned cluster + bastion for verifying `kafkareport`
end-to-end before publishing. Two flavors, picked by which CFN template
you deploy:

| Auth | Template | Cluster |
| --- | --- | --- |
| SASL/SCRAM | `msk.yaml` | Provisioned |
| IAM (`AWS_MSK_IAM`) | `msk-iam.yaml` | Provisioned |

Both cost roughly **~$280+/month** at default size (`kafka.m7g.large` × 2
+ storage). **Tear it down when you're done either way.**

MSK Serverless is not supported by `kafkareport` — it doesn't expose
`DescribeLogDirs`, which is the entire basis of the size report.

> [!WARNING]
> LLM-created for throwaway smoke tests. Use only on nonproduction accounts.

## Deploy

### SCRAM

```sh
aws cloudformation deploy \
  --stack-name kafkareport-livetest \
  --template-file livetest/msk.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      KafkaUsername=kafkareport \
      KafkaPassword='<pick a 12+ char password>'
```

### IAM

```sh
aws cloudformation deploy \
  --stack-name kafkareport-livetest-iam \
  --template-file livetest/msk-iam.yaml \
  --capabilities CAPABILITY_IAM
```

MSK provisioning takes 15–25 minutes either way.

## Grab the outputs

```sh
aws cloudformation describe-stacks --stack-name <your-stack-name> \
  --query 'Stacks[0].Outputs' --output table
```

You'll get a `ClusterArn` and a `BastionInstanceId` from either stack.

## Build the conf file

### SCRAM

```sh
aws kafka get-bootstrap-brokers --cluster-arn <ClusterArn> \
  --query BootstrapBrokerStringSaslScram --output text
```

```json
{
  "bootstrap.servers": "<the BootstrapBrokerStringSaslScram value>",
  "security.protocol": "SASL_SSL",
  "sasl.mechanism": "SCRAM-SHA-512",
  "sasl.username": "kafkareport",
  "sasl.password": "<the password you set>"
}
```

### IAM

```sh
aws kafka get-bootstrap-brokers --cluster-arn <ClusterArn> \
  --query BootstrapBrokerStringSaslIam --output text
```

Note the `AWS_MSK_IAM` sentinel; no user/pass:

```json
{
  "bootstrap.servers": "<the BootstrapBrokerStringSaslIam value>",
  "sasl.mechanism": "AWS_MSK_IAM"
}
```

Credentials come from the default AWS chain. On the bastion that's the
instance profile (which the IAM stack grants `kafka-cluster:*` on the
cluster's topics and groups). Region is exported into the bastion's shell
at launch by user-data, so `AWS_REGION` is already set when you SSM in.

## Run from the bastion

The MSK brokers live in private subnets. Easiest way in is the bastion the
stack provisioned, via SSM (no SSH key needed):

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
