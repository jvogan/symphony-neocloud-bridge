# AWS And RunPod Superpowers

Checked against official RunPod and AWS docs on 2026-05-02; rechecked key RunPod registry, billing, Flash, and network-volume S3 docs on 2026-05-05.

AWS should be an optional control-plane and artifact-plane companion for RunPod, not a hard dependency. The bridge contract stays provider-neutral; AWS-specific mechanics live in runtime references, optional egress modes, or orchestrator adapters.

Use `cloud-bridge aws-orchestrator-plan <manifest>` before adding AWS-backed orchestration. It renders command templates, helper JSON templates, and required environment refs for every companion surface below without executing AWS or RunPod mutations. When helper files contain `$ENV` placeholders, the rendered plan includes a `python3` expansion step so the final AWS CLI commands receive concrete JSON only on the trusted orchestrator host.

Reference examples:

- `examples/aws-orchestrated/launch_manifest.json` demonstrates STS-scoped S3 artifact upload, ECR registry refresh, Secrets Manager refs, SQS handoff, DynamoDB launch lock, and EventBridge cleanup backstop.
- `examples/runpod-network-volume-s3/launch_manifest.json` demonstrates artifact pull through RunPod's S3-compatible network-volume API plus normal pod cleanup.

## Adopt Now

1. `aws_s3_presigned_upload` artifact egress
   - Use when a pod should upload an archive to AWS S3 without receiving AWS access keys.
   - The trusted orchestrator generates short-lived S3 PUT URLs for the archive and optional hash ledger, injects them as runtime env vars, and never writes the URLs to the manifest or Linear.
   - The pod uses `curl --upload-file` when available, with a Python stdlib PUT fallback for slim base images; closeout validates S3 object presence and hashes from the orchestrator side.
   - Manifest fields: `artifact_egress.mode: aws_s3_presigned_upload`, `archive_upload_url_ref`, optional `hash_upload_url_ref`, and `requires_presigned_upload: true`.

2. Runtime-injected `object_store_upload`
   - Use for long-running jobs that need AWS CLI multipart upload, `sync`, or more than one destination object.
   - Credentials must be short-lived STS credentials or secure-store references resolved by the orchestrator.
   - Prefer session policies that only allow the run prefix, for example `s3://bucket/runs/<run_id>/`.
   - Use `credentials_ref: aws-sts:<role-arn>` or `aws.artifact_role_arn_ref` so `aws-orchestrator-plan` can render `aws sts assume-role` plus a scoped S3 PUT policy.

3. RunPod network-volume S3 plus AWS-compatible tooling
   - Use when artifacts should survive pod deletion and can be pulled from the retained network volume without keeping compute alive.
   - RunPod exposes network volumes through S3-compatible endpoints, using separate S3 API keys and AWS CLI/Boto3-compatible commands.
   - This is not AWS S3, but it benefits from the same operator tooling and closeout shape.

4. ECR image lane
   - Build and scan images in AWS, push to ECR, refresh registry auth immediately before launch, and register that auth with RunPod only from the trusted orchestrator.
   - ECR auth tokens are time-limited, so the bridge should avoid storing registry passwords in manifests, issues, or repo files.
   - RunPod-side registry auth must be provider-side, for example a `runpodctl registry create` record whose ID is referenced by the launch manifest.
   - Good future command: `registry-refresh --provider ecr --registry-auth-name <name>`.

5. AWS Secrets Manager references
   - The manifest already accepts `aws-sm:` as a safe reference prefix.
   - Workers should carry references only. Orchestrators resolve secrets immediately before mutation and redact values from logs.
   - `aws-orchestrator-plan` lists each `aws-sm:` or `aws-secretsmanager:` reference and renders `aws secretsmanager get-secret-value` commands as sensitive operator-only steps.

## Orchestrator Controls

- SQS handoff queue: publish `provider_handoff.json` packets to a queue, use visibility timeout extension as the launch heartbeat, delete only after artifact proof and cleanup, and send repeated failures to a DLQ.
- DynamoDB launch lock: replace or supplement local lock files with conditional writes plus TTL for multi-host orchestrators.
- EventBridge Scheduler cleanup backstop: schedule a one-time cleanup call at `terminate_after_minutes` in case the local orchestrator dies.
- AWS Budgets and anomaly alerts: watch the AWS side of artifact storage, queues, and orchestrators. RunPod spend still needs RunPod billing/cost records.
- CloudWatch metrics and logs: publish bridge records, cleanup results, artifact hashes, and billing closeout summaries for fleet dashboards.
- S3 Object Lock or immutable buckets: optional for regulated artifact ledgers, but do not make it the default path.

## Skip Or Keep Optional

- Do not require AWS for ordinary RunPod runs. Local dry-run, RunPod-only pod lifecycle, and network-volume egress must remain first-class.
- Do not put long-lived AWS credentials inside pods. Prefer presigned URLs or short-lived STS credentials with narrow policies.
- Do not treat successful upload as scientific success. It is only artifact transport; closeout still needs declared artifacts, hash proof, validation commands, and cleanup verification.
- Do not use S3 object existence as the only proof for huge runs. Require a hash ledger, status file, and stage done markers.

## Source Notes

- RunPod S3-compatible API supports network-volume file access through AWS CLI and Boto3-compatible operations with datacenter-specific endpoints and separate S3 API credentials: https://docs.runpod.io/storage/s3-api
- RunPod private registry auth can be managed with `runpodctl registry`: https://docs.runpod.io/runpodctl/reference/runpodctl-registry
- RunPod Flash currently documents Python 3.12, macOS/Linux support, CPU endpoints in `EU-RO-1`, and Flashboot endpoint parameters: https://docs.runpod.io/flash and https://docs.runpod.io/flash/configuration/parameters
- AWS S3 presigned PUT URLs allow upload without giving the uploader AWS credentials, bounded by the signer permissions and URL expiration: https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html
- AWS STS `AssumeRole` returns temporary credentials and can be constrained with session policies: https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html
- AWS ECR `get-login-password` produces a registry auth token valid for 12 hours: https://docs.aws.amazon.com/cli/latest/reference/ecr/get-login-password.html
- AWS Secrets Manager `get-secret-value` retrieves secret material and its output must be treated as sensitive: https://docs.aws.amazon.com/cli/latest/reference/secretsmanager/get-secret-value.html
- SQS visibility timeouts and DLQs fit queue-backed handoff processing: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- AWS CLI `sqs send-message`, `receive-message`, `change-message-visibility`, and `delete-message` are the operator primitives for queue-backed handoffs: https://docs.aws.amazon.com/cli/latest/reference/sqs/send-message.html
- EventBridge Scheduler supports one-time schedules and `create-schedule` with target and DLQ settings for cleanup backstops: https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule.html and https://docs.aws.amazon.com/cli/latest/reference/scheduler/create-schedule.html
- DynamoDB condition expressions, `put-item`, and TTL fit distributed launch locks with expiry: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.OperatorsAndFunctions.html, https://docs.aws.amazon.com/cli/latest/reference/dynamodb/put-item.html, and https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/time-to-live-ttl-before-you-start.html
