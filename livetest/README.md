# Live test against real MSK

> [!WARNING]
> LLM-created for throwaway smoke tests. Use only on nonproduction accounts.

A throwaway MSK provisioned cluster + bastion for verifying `kafkareport` end-to-end before publishing. Two flavors, picked by which CFN template you deploy:

| Auth | Template | Cluster |
| --- | --- | --- |
| SASL/SCRAM | `msk.yaml` | Provisioned |
| IAM (`AWS_MSK_IAM`) | `msk-iam.yaml` | Provisioned |

Both cost roughly **~$280+/month** at default size (`kafka.m7g.large` × 2 + storage). **Tear it down when you're done either way.**

## Deploy

### SCRAM

```sh
STACK_NAME=kafkareport-livetest
KAFKA_PASSWORD=$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 13); echo "$KAFKA_PASSWORD"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file livetest/msk.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      KafkaUsername=kafkareport \
      KafkaPassword="$KAFKA_PASSWORD"
```

### IAM

```sh
STACK_NAME=kafkareport-livetest-iam

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file livetest/msk-iam.yaml \
  --capabilities CAPABILITY_IAM
```

MSK provisioning takes 15–25 minutes either way.

## Grab the outputs

```sh
CLUSTER_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ClusterArn'].OutputValue" --output text)
BASTION_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='BastionInstanceId'].OutputValue" --output text)
```

## Build the conf file

### SCRAM

```sh
BROKERS=$(aws kafka get-bootstrap-brokers --cluster-arn "$CLUSTER_ARN" \
  --query BootstrapBrokerStringSaslScram --output text)

cat <<EOF
Copy this and paste into conf.json on the bastion host below:
{
  "bootstrap.servers": "$BROKERS",
  "security.protocol": "SASL_SSL",
  "sasl.mechanism": "SCRAM-SHA-512",
  "sasl.username": "kafkareport",
  "sasl.password": "$KAFKA_PASSWORD"
}
EOF
```

### IAM

Note the `AWS_MSK_IAM` sentinel; no user/pass:

```sh
BROKERS=$(aws kafka get-bootstrap-brokers --cluster-arn "$CLUSTER_ARN" \
  --query BootstrapBrokerStringSaslIam --output text)

cat <<EOF
Copy this and paste into conf.json on the bastion host below:
{
  "bootstrap.servers": "$BROKERS",
  "sasl.mechanism": "AWS_MSK_IAM"
}
EOF
```

Credentials come from the default AWS chain. On the bastion that's the
instance profile (which the IAM stack grants `kafka-cluster:*` on the
cluster's topics and groups). Region is exported into the bastion's shell
at launch by user-data, so `AWS_REGION` is already set when you SSM in.

## Run from the bastion

The MSK brokers live in private subnets. Easiest way in is the bastion the
stack provisioned, via SSM (no SSH key needed):

```sh
aws ssm start-session --target "$BASTION_ID"
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
aws cloudformation delete-stack --stack-name "$STACK_NAME"
```

This deletes the cluster, bastion, VPC, and (for SCRAM) the KMS key and
secret. The KMS key is scheduled for deletion (7 day default window); the
rest goes immediately.
