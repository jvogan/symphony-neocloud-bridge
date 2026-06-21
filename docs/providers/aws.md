# AWS Provider Reference

## Overview / positioning

AWS plays **two distinct roles** in the cloud bridge. Do not conflate them.

1. **AWS-as-orchestration glue.** The bridge ships an AWS orchestrator planner (`build_aws_orchestrator_plan`, CLI `aws-orchestrator-plan`) that *renders but never executes* command templates for: STS-scoped S3 upload, presigned S3 PUT egress, ECR registry refresh, Secrets Manager resolution, SQS handoff, DynamoDB launch locks, and an EventBridge cleanup backstop. These wrap a run on any compute provider; here AWS is the **artifact plane + control plane**, not the compute.
2. **AWS-as-compute (provider playbook).** AWS Batch and EC2 provider profiles exist as **planning contracts** in this bridge — no `boto3`, no run logs. Treat the profiles as specs that need a public smoke before they are trusted.

Guiding principle: **AWS is an optional companion, not a hard dependency.** The portable contract stays provider-neutral; AWS specifics live in runtime references, optional egress modes, and orchestrator adapters. Local dry-run and the RunPod-only path stay first-class.

Selection order across the stack: **RunPod -> AWS Batch (scale / multi-shard) -> Modal canary -> generic cloud VM -> local.**

## Launch & command rendering

### AWS-as-orchestration planner

```
cloud-bridge aws-orchestrator-plan <manifest.json> [--handoff provider_handoff.json]
```

Returns `{ok, required_env, blockers, warnings, features{...}}` for seven companion surfaces. Each `feature` carries `status` (`configured` / `recommended` / `blocked` / `not_configured` / `available`), `commands`, `required_env`, `warnings`, `helper_files`. Helper JSON files containing `$ENV` placeholders ship with a `python3 -c` `os.path.expandvars` render step, so concrete JSON is produced only on the trusted orchestrator host.

### AWS-as-compute (target patterns)

- **EC2 one-shot (target profile)** — mirrors a RunPod pod: upload `boot.sh` to S3, then `aws ec2 run-instances` with a *tiny* `--user-data` that fetches and execs `boot.sh` from S3. An EBS gp3 volume substitutes for RunPod's network volume. **Hard limit: user-data is 16 KB raw** (`InvalidUserData.MalformedFileSize`; the SDK allows ~21847 base64 bytes) — *tighter* than RunPod's 64 KB, which is exactly why the S3-fetch indirection is mandatory (a boot script in S3 has no size limit). The image arg is optional: empty runs `boot.sh` on the bare AMI; otherwise the wrapper does `docker run <IMAGE> bash boot.sh`. On AL2023 you must `dnf install -y docker && systemctl start docker` first (Deep Learning AMIs ship Docker).
- **AWS Batch (blessed scale path; contract-only)** — `provider_class=batch_job`, `blessed_path=true`. Declares its launch surface by env-ref indirection (never literals): `*_AWS_BATCH_COMPUTE_ENV`, `_JOB_QUEUE`, `_JOB_DEFINITION`, `_ARTIFACT_BUCKET`, `_CLOUDWATCH_LOG_GROUP`, `_BUDGET_NAME`. Batch is the multi-shard GPU fanout / RunPod-fallback lane and diversifies capacity for you. Implement as `boto3` `submit_job`.
- **EC2 GPU VM (rescue/debug path; contract-only)** — `provider_class=cloud_vm`, `blessed_path=false`. Adds `instance_profile_ref`, `security_group_ref`, `subnet_ref`; requires a `network_security_review`.

Resolve images and AMIs without rotting hardcodes:

- AMI via SSM (keep as primary; any fallback AMI ID rots): `aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value`.
- Default subnet via `describe-subnets Name=default-for-az,Values=true`; security group by tag.
- `image_strategy=ecr_digest_or_public_base_plus_bootstrap` — digest-pin the ECR image or use a public base + bootstrap (mirrors RunPod's GHCR-digest requirement).

**Capacity:** `InsufficientInstanceCapacity` is AWS's analog of RunPod's "no instances available". Mitigate with a Launch Template + multi-AZ ASG + Mixed Instances Policy, or use Batch (it diversifies for you).

Container/orchestration decision tree: **EC2+Docker** for RunPod-shaped one-shots (default); **ECS Fargate** for serverless containers (but >5 GB images can 30-min-stall); **AWS Batch** for hundreds-to-thousands of independent Spot-aware jobs; **ParallelCluster** for true MPI/HPC. Managed pipeline runners cover only their built-in pipelines.

### Render startup

- **EC2:** keep `--user-data` minimal (<=16 KB) — pull and exec `boot.sh` from S3 only. The real startup logic lives in the S3-staged boot script (the AWS analog of the bridge's `render-startup` output).
- **Batch:** the job definition + container command is the startup surface; ECR-digest-pinned (or public base + bootstrap).
- **ECR registry refresh helper:** `ecr_registry_refresh_plan` detects an ECR image (regex on `\d{12}.dkr.ecr.<region>.amazonaws.com/...`) and renders, **with shell tracing disabled**:

  ```
  set +x
  RUNPOD_ECR_PASSWORD="$(aws ecr get-login-password --region $AWS_REGION)"
  runpodctl registry create --name <run>-ecr --username AWS --password "$RUNPOD_ECR_PASSWORD"
  unset RUNPOD_ECR_PASSWORD
  runpodctl registry list
  ```

  ECR tokens are time-limited (12h) — refresh immediately before launch and delete stale registry-auth records. The created auth ID must be injected into `runpod.containerRegistryAuthId` before launch.

## Authentication

**Governing rule: no long-lived credentials in the workload, and never paste keys into chat / issue tracker / repo / `.env`.** AWS gives credential-less mechanisms, layered by role:

- **EC2 / Batch in-instance: IAM instance profile + IMDSv2.** Attach an instance profile; the CLI/SDK inside the instance authenticates automatically. This **eliminates the entire RunPod stale-injected-API-key footgun class.** IMDSv2 is mandatory on new AMIs: `PUT http://169.254.169.254/latest/api/token` with header `X-aws-ec2-metadata-token-ttl-seconds`, then pass `X-aws-ec2-metadata-token` on metadata GETs. Secrets via IAM role + Secrets Manager (the `iam_role_and_aws_secrets_manager` resolution mode), never baked keys.
- **Credential-less egress from a pod you don't trust: presigned S3 PUT** (see Egress) — the worker PUTs with zero AWS creds.
- **Short-lived multipart/sync from the orchestrator: STS `assume-role`** with a session policy scoped to the run prefix (`sts_object_store_plan`). Duration is `(max_runtime_minutes + 15) * 60`, clamped to 900–43200s.
- **Secret references (provider-neutral helper).** `SAFE_REF_RE` accepts these credential-ref prefixes anywhere in a manifest: `$`, `env:`, `secret:`, `secure-store:`, `vault:`, `aws-sm:`, `aws-secretsmanager:`, `aws-sts:`, `gcp-sm:`, `azure-kv:`, and `{{ RUNPOD_SECRET_* }}`. Every provider's credentials are a named runtime ref resolved only on the trusted orchestrator, never a literal in any durable file. `secrets_manager_plan` enumerates each `aws-sm:`/`aws-secretsmanager:` ref and renders `aws secretsmanager get-secret-value --query SecretString --output text` as a sensitive operator-only step.

Operator auth boundary: first smoke = AWS Console + MFA -> CloudShell in-region; repeat = `aws configure sso --profile X` + `aws sso login` + `aws sts get-caller-identity`. Self-terminate from inside via an instance role scoped with `Condition StringEquals ec2:ResourceTag/project=<x>`, so self `TerminateInstances`/`StopInstances` can only touch instances you tagged.

## Monitoring / observability — and what is NON-authoritative

**Critical theme: status commands lag; artifacts are the only authoritative success proof.** This matches the bridge's existing stance that HTTP-proxy / TCP inspection are non-authoritative.

Non-authoritative / misleading signals:

- Managed job/pipeline `status` commands **lag actual progress**; CloudWatch logs are the best real-time per-phase signal.
- **AWS Budgets actual spend is a delayed signal** — immediately after completion, Budget actual spend can still report **$0.00**. Do not use budget actual-spend as cost truth right after a run.
- A single CPU/GPU utilization sample does not prove useful progress (same caveat the bridge applies to RunPod `pod.runtime`).
- **There is no free public proxy URL like RunPod's `*.proxy.runpod.net` on AWS** — the single biggest observability thing you lose leaving RunPod. Build it yourself: SSM Session Manager, a public-IP `http.server`, or CloudWatch Logs.

Authoritative / recommended:

- **CloudWatch Logs** for real-time phase progress (`cloudwatch_log_write` is a Batch preflight check).
- **Artifact-based success proof:** fetch the result file, record its **SHA-256**, parse output ledgers, write a cleanup proof. The single best success proof was artifact + hash, not the status command.
- **S3 status sidecar:** `aws s3 cp STATUS/SUCCESS/FAILURE s3://bucket/<tool>/<run>/status/` every ~30s; final `aws s3 sync` of artifacts on exit. Monitor via `aws s3 cp s3://.../STATUS -`. **No `?cb=$RANDOM` cache-bust needed:** S3 has strong read-after-write consistency and GETs aren't AWS-cached — *unless* fronted by CloudFront.

## Durable artifact egress

The bridge enumerates AWS-aware egress modes (`workspace_archive`, `network_volume`, `runpod_network_volume_s3`, `scp`, `object_store_upload`, `aws_s3_presigned_upload`). The two AWS-real durable lanes:

1. **`aws_s3_presigned_upload` — the no-credentials-in-pod lane.** A presigned PUT URL lets the worker `curl --upload-file <archive> "$URL"` (Python stdlib PUT fallback for slim images) with **zero AWS creds in the pod**. The validator **HARD-ERRORS** if a literal `archive_upload_url`/`upload_url` is placed in the manifest ("presigned URLs are bearer credentials") — only `archive_upload_url_ref`/`upload_url_ref`/`hash_upload_url_ref` are allowed, and `requires_presigned_upload: true` fails closed on missing URLs. Closeout validates S3 object presence + hash from the orchestrator side.
2. **`object_store_upload` — the push-to-a-bucket-I-own lane.** Requires `destination_uri`/`destination_uri_ref` AND a `credentials_ref` (warns/errors when missing, depending on `remote_launch_allowed`). Best paired with STS short-lived creds scoped to the run prefix. Renders `aws s3 cp <archive> <dest>/` plus the `artifact_hashes.jsonl` ledger.

Batch/EC2-native pattern: `artifact_root = s3://<bucket>/runs/<run-id>`, `artifact_egress = s3_checksum_required` — the worker PUTs to S3 (no inbound creds) and integrity is verified by **S3 checksum**. This generalizes to presigned PUT for credential-less egress; every provider pairs egress with a SHA-256/checksum gate.

Full S3 upload+verify recipe (reusable for any S3-compatible store): `aws s3 cp --region <r> --endpoint-url <ep> <archive> s3://<bucket>/<key>` -> `aws s3api head-object` to confirm landing -> `sha256sum` and compare against the manifest's declared `archive_sha256` before launch.

**Do not over-trust S3 object existence.** Successful upload is transport, not workload success — closeout still needs declared artifacts, a hash ledger, status file, and stage done-markers (especially for `large`/`huge` runs, which the egress planner nudges to pair with a `checkpoint_policy`).

### RunPod S3 is NOT AWS S3 — branch the strategy

- RunPod's S3-compatible endpoint is `https://s3api-{datacenter}.runpod.io/`; keys are **separate** from `RUNPOD_API_KEY` and stay on the trusted orchestrator.
- **RunPod S3 has NO presigned-URL path.** For RunPod you stage to a network-volume snapshot (`aws s3 cp` + `aws s3api head-object`) and *mount* it, rather than handing the pod a presigned URL. Real AWS S3 supports presigned PUT; RunPod's does not (`runpod_network_volume_s3_plan` warns about both gaps).

**Same-region data win:** free multi-Gbps pulls from public Open Data S3 mirrors (`aws s3 cp ... --no-sign-request`) vs RunPod's 1–4 MB/s internet ceiling. AWS gives 100 GB/month internet egress free (aggregated), then tiers ~$0.09 -> $0.05/GB.

## Cost & budget control

**AWS Budgets are delayed alerts, not real-time hard caps.** Build real guardrails:

- **Real-time hard stop = an AWS Budgets ACTION** (`type APPLY_IAM_POLICY`, `AUTOMATIC` approval, `STANDBY` status) that auto-attaches an emergency Deny policy at a low absolute actual-spend threshold (e.g. $30). The Deny policy denies `ec2:RunInstances`, `batch:SubmitJob`, `cloudformation:CreateStack`. The probe verifies the action is on **standby** (if already applied, live submits are already blocked).
- **Layered IAM Deny guardrails:** a `DenyOutsideRegion` statement (`Condition StringNotEquals aws:RequestedRegion`) region-locks all activity; a `DenyHugeOrSpecialtyEC2` statement denylists expensive/specialty instance families. **Explicit Deny wins even over `AdministratorAccess`** — a broad-access user can still be hard-fenced.
- **Budget as a launch gate.** Every AWS profile requires an `aws_budget_alert` (`*_AWS_BUDGET_NAME`) as an execution-ready precondition — budget guard is a launch gate, mirroring Modal per-wave caps and Lambda single-instance discipline.
- **Estimate cost from instance-type x runtime**, then reconcile after billing lag — don't trust actual-spend right after a run.

Storage cost reference (`us-east-1`): `gp3` ~$0.08/GB-mo (3000 IOPS + 125 MB/s free); S3 Standard ~$0.023/GB-mo; Glacier Deep Archive ~$0.00099/GB-mo; EFS Standard ~$0.30/GB-mo. Compute (on-demand / Spot): `c6i.4xlarge` 16c/32GB ~$0.68 / ~$0.20; `g5.xlarge` A10G ~$1.006; A100 40GB ~$3.67 / ~$1.10; H100 p5 ~$5.50–7.20 / ~$3.00. **AWS wins on huge same-region data pulls and >=1000-job Spot batches; loses on GPU-under-a-few-hours and egress past the 100 GB/mo free tier.** Spot interrupts mid-pipeline: checkpoint or use On-Demand for runs <60 min. Picking a `g5.*` for a CPU job is the closest analog to RunPod's silent-GPU-billing footgun (but explicit, user error).

EBS storage decision tree: read large public datasets directly from Open Data S3 (don't copy); reusable reference data -> gp3 snapshot restored per instance (or S3 + on-boot rsync if parallel); per-run scratch -> instance-store NVMe (free, dies on stop) on i4i/c6id/r6id; final results -> S3 Standard; cold -> Glacier Deep Archive; concurrent multi-writer -> EFS Elastic or FSx Lustre.

## Cleanup / closeout

**Cleanup policy differs per provider-class and must be modeled separately:**

- **Batch:** `job_terminal_plus_s3_lifecycle_or_explicit_retention` — job reaches terminal state + an **S3 lifecycle policy** handles artifact retention (the cost-control mechanism).
- **EC2:** `terminate_instance_unless_explicit_retention` — you **must explicitly terminate** the instance (same forgot-to-terminate hourly-billing risk as Lambda, including the stopped-EBS ~$0.08/GB-mo cost if you only stop).

Self-stop patterns, with the **sentinel-before-call** rule (write `.self_stop_status` to S3 **before** calling stop, then check the exit code):

- **(A)** `shutdown -h` with `InstanceInitiatedShutdownBehavior=stop` — no IAM, but you keep paying for stopped EBS.
- **(B)** `aws ec2 stop-instances --instance-ids <self>` via IMDSv2 (preferred; swap to `terminate-instances` to also reclaim EBS).
- **(C)** CloudWatch idle-CPU alarm (CPU<5% for 3x5min -> stop) as an operator-side backstop for orphaned instances.

Tagging discipline for safe cleanup: tag every instance + volume with `project=<x>, tool=<tool>, <project>-run-id=<run>`. **NEVER bulk-`terminate-instances` on raw IDs from `describe-instances`** — always filter `Name=tag:<project>,Values=worker` first. Cleanup verification is explicit and tag-scoped: after delete, verify in-region that EC2 instances (by tag + state), Batch compute-environments + job-queues, and CloudFormation stacks are gone; record `cleanup_status` in the run ledger. S3 hygiene: remove short-lived scratch prefixes after local fetch; retain S3 results only when explicitly requested; bucket needs block-public-access (all 4 flags) + default encryption (AES256/aws:kms) + lifecycle.

**Orchestrator-side cleanup backstop helper:** `eventbridge_cleanup_plan` renders an **EventBridge Scheduler one-time** `create-schedule` (`at(<terminate_after_minutes>)`, `--action-after-completion DELETE`, optional DLQ) that fires a cleanup call if the local orchestrator dies. It is a *backstop only* — normal bridge cleanup and artifact closeout still own success.

## Adapter implications

Mapping the learnings onto our portable contract stages.

- **validate-manifest / validate-handoff:** Reuse `SAFE_REF_RE` (`aws-sm:`/`aws-secretsmanager:`/`aws-sts:` already accepted); keep the literal-presigned-URL hard-error. An AWS-compute adapter must additionally validate an `aws_account_region_allowlist`, an `aws_budget_alert` name, a digest-pinned ECR image (or bootstrap), and the standing-infra refs (compute-env/job-queue/job-definition for Batch; instance-profile/security-group/subnet for EC2).
- **prepare:** AWS needs **standing infrastructure provisioned first** — unlike spin-up-from-nothing RunPod pods. Implement the Batch `execution_ready_requires` checklist (region allowlist, GPU quota, compute-env/queue/definition existence, ECR auth, S3 bucket, CloudWatch log group, budget alert, pinned git ref, input audit, explicit operator launch) and the quota checks (`ec2_gpu_instance_quota` — the usual blocker — `batch_vcpu_quota`, `ecr_pull_access`, `s3_put_get_checksum`, `cloudwatch_log_write`). One-time bootstrap (~15 min): `aws s3 mb` dispatch bucket; IAM role with EC2 trust + scoped S3/EC2-tag-conditioned policy; instance profile.
- **write-handoff:** Carry only runtime refs (bucket, region, budget name, ECR digest, standing-infra refs) into the handoff — never literal account IDs, keys, or presigned URLs; those resolve on the orchestrator.
- **render-startup:** EC2 -> minimal <=16 KB user-data that fetches+execs an S3-staged `boot.sh` (the 16 KB cap forces this indirection). Batch -> ECR-digest-pinned (or public + bootstrap) job-definition command. AL2023 needs an explicit `dnf install docker`.
- **source-checkout:** S3-staged boot script (no size limit) for EC2; EBS gp3 snapshot or instance-store NVMe substitutes for RunPod's network volume; read Open Data S3 mirrors directly for huge same-region inputs.
- **poll-state:** Poll Batch job state / EC2 instance state, but treat status as **lagging**; gate GPU launches on `ec2_gpu_instance_quota` first; handle `InsufficientInstanceCapacity` (Launch Template + multi-AZ ASG, or Batch).
- **capture-evidence:** CloudWatch Logs for real-time phase progress + an S3 STATUS/SUCCESS/FAILURE sidecar (every ~30s, no cache-bust). **No managed public proxy URL exists** — the adapter must provision SSM / `http.server` / CloudWatch instead. Status commands are explicitly non-authoritative; the success-of-record is the **fetched + SHA-256-hashed artifact** verified via `head-object` + hash.
- **egress-plan:** `aws_s3_presigned_upload` (credential-less) and `object_store_upload` (own-bucket + STS) are bridge-supported egress modes; the Batch/EC2-native form is `artifact_root=s3://.../runs/<run-id>` + `s3_checksum_required`. **Branch egress per provider** (AWS S3 has presigned; RunPod S3 does not).
- **budget-limits:** Budget alert is a launch gate; for a real hard stop wire an AWS Budgets **APPLY_IAM_POLICY action** + IAM Deny guardrails (region-lock, instance-family denylist) — explicit Deny beats AdministratorAccess. STS duration derives from `budget.max_runtime_minutes`.
- **billing-report:** **Do not read AWS Budgets actual-spend as cost truth right after a run** (delayed; reported $0.00). Estimate from instance-type x runtime and reconcile after billing lag.
- **cleanup-closeout:** Per-class policy — EC2 must explicitly terminate (forgot-to-terminate = hourly-bill + stopped-EBS bleed); Batch uses S3 lifecycle. Self-stop with sentinel-before-call; tag-scoped cleanup verification (never bulk-terminate raw IDs); EventBridge Scheduler one-time backstop already rendered.
- **supervise:** Treat the local orchestrator as fallible — the EventBridge backstop and the CloudWatch idle-CPU alarm are the supervisory safety nets if the supervisor dies mid-run; neither replaces normal closeout.
- **dashboard:** No managed dashboard ships; surface CloudWatch Logs + the S3 status sidecar (and any self-provisioned SSM/`http.server`) as the operator view. Account IDs and identifiers must be redacted before display.
- **symphony-outcome:** Generator stays prep-only / never touches AWS (writes reviewable `.ini`/TSV ledgers under a git-ignored `.runtime/`); a separate human-reviewed step runs the real submit. **Redact 12-digit account IDs and `AIDA*`/`AROA*`/ARN identifiers** before logging any outcome. Outcome contract: raw archives stay in S3; only compact artifacts (counts, summaries, provenance with config/region/result-URI, cleanup + cost summary) come back.

**Cross-provider rule (holds for AWS too):** provider RUNNING / exit-0 / app-logs is **NEVER** workload success. Closeout requires fetched + hashed artifacts, a billing reconciliation, and verified (tag-scoped) cleanup. The `provider_class`-keyed `cleanup_policy` + `execution_ready_requires` is the clean seam for a uniform `ProviderAdapter` interface (the profile contract — `provider`, `provider_class`, `profile_id`, `workspace_root`, `artifact_root`, `secret_mode`, `operator_gate_required`, `execution_ready_requires` — is already uniform across RunPod / Modal / AWS / generic-VM / SSH-HPC / local).

## Open questions

- **No AWS-as-compute adapter exists in this bridge.** The Batch/EC2 profiles are *contracts, not code* — no `boto3`, no run logs. An implementer must fill in `boto3` Batch `submit_job` / EC2 `run_instances` + S3-checksum egress + CloudWatch polling, then validate with a public smoke.
- **Spot interruption resume semantics** are unspecified — need a concrete checkpoint/resume contract before defaulting Batch/EC2 to Spot for >60-min runs.
- **GPU quota provisioning lead time** is unmodeled — `ec2_gpu_instance_quota` is the usual blocker, but how far ahead to request increases (and the interaction with a low-quota safety posture) is open.
- **Public progress channel** — which substitute for RunPod's proxy URL to standardize on (SSM vs `http.server` vs CloudWatch dashboard) is undecided.
- **Long-running egress with multipart** — STS duration is clamped at 43200s (12h); workloads longer than that need a credential-refresh strategy not yet designed.
- **Fargate / ParallelCluster / managed pipeline runners** are decision-tree options, not adapters — no contract shape defined yet.
- **ECS Fargate >5 GB image 30-min stall** and public-registry image deprecations are noted hazards without a tested mitigation in our flow.
