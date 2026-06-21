# RunPod Bridge CLI Reference

Use this reference when the short happy path in `SKILL.md` is not enough.

## Local Validation And Planning

- `doctor`: check bridge and skill discoverability.
- `audit-manifests`: scan a repo or template directory for RunPod launch manifests and stale copied bundle shapes. Use `--migration-hints --summary-only` for large downstream repos, then drop `--summary-only` or add `--only-failures` for per-file cleanup.
- `audit-runpod-ops`: scan repo text for direct RunPod REST mutation, local app config key scraping, and split create/cleanup recipes that can bypass bridge closeout. Use `--summary-only` for large repos.
- `validate-manifest`: validate manifest shape and launch gates.
- `contract-self-check`: check stage contract, route proof, artifact proof, monitoring truth, and claim boundaries.
- `validate-linear-issue`: validate a Linear issue body.
- `issue-intake`: validate an issue plus manifest and prepare a handoff packet.
- `render-startup`: render the startup script without launching.
- `render-runpodctl-create`: render the equivalent `runpodctl pod create` command, including `--terminate-after`, without launching.
- `plan`: print the dry-run execution plan.
- `preflight`: run launch, profile, provider, contract, bootstrap image-capability, payload-size, productivity, and egress checks.
- `egress-plan`: render artifact egress requirements, including AWS S3 presigned upload env refs when declared.
- `productivity-plan`: render live progress and peek-channel checks; distinguish provider `RUNNING`, workload productivity, SSH/log tail, and completion-only artifact inspection.
- `source-check`: check git source/ref reachability before paid launch. Use `--execute` only when network access and git credentials are expected to work.
- `source-ingress-plan`: render private source ingress commands for prepared snapshots, archive URLs, and RunPod network-volume S3 staging.
- `registry-auth-plan`: render provider-side private image registry auth checks and exact image-pull canary expectations.
- `aws-orchestrator-plan`: render optional AWS companion commands for STS-scoped uploads, RunPod network-volume S3, ECR registry auth refresh, Secrets Manager refs, SQS handoff queues, DynamoDB launch locks, and EventBridge cleanup backstops.
- `profiles`: list or recommend compute profiles.
- `provider-capabilities`: describe provider adapter support.
- `public-audit`: check public-release readiness.

## Packet And Local Execution

- `prepare`: write `launch_manifest.json`, `startup.sh`, `local_preflight.json`, and `provider_handoff.json`.
- `write-handoff`: write a provider handoff for orchestrator-side execution.
- `validate-handoff`: validate a provider handoff and referenced manifest.
- `run-local`: execute the rendered startup contract locally.
- `monitor`: inspect local workload heartbeat, status, and log files.
- `supervise`: recommend the next action from local workload state.
- `closeout`: hash artifacts and write local closeout files.
- `remote-outcome`: render a Linear-ready `symphony-outcome` block from a remote run or handoff record.
- `dashboard`: render a local HTML dashboard from run records.
- `recover-run`: analyze or execute recovery for a run record.

## Orchestrator And Remote Mutation

- `run-handoff`: consume `provider_handoff.json`, create a guarded pod, verify artifacts, and attempt cleanup.
- `run-remote`: run the same guarded create/verify/cleanup flow directly from a manifest.
- `create-pod`: build or execute an audited RunPod pod creation request.
- `cleanup-pod`: build or execute an audited stop/delete request.
- `orchestrator-scan`: scan a directory tree for provider handoffs.
- `orchestrator-once`: run ready provider handoffs once from an orchestrator directory.

Remote mutation requires explicit execute and confirmation flags, for example `--execute --yes-create-paid-runpod --yes-cleanup-runpod`.

## RunPod Read-Only And Billing

- `list-pods`: list RunPod pods.
- `get-pod`: fetch one RunPod pod.
- `gpu-catalog`: probe RunPod GraphQL `gpuTypes` for requested GPU IDs, data centers, cloud type, and current availability before REST create retries.
- `runtime-metrics`: fetch RunPod GraphQL `pod.runtime` uptime/utilization fields and flag likely crash loops from tiny or resetting uptime.
- `progress-report`: combine pod state, runtime metrics, and live `/healthz` into one read-only progress classification; monitor liveness is reported separately from workload progress.
- `pod-ssh-info`: fetch SSH command details through `runpodctl ssh info`.
- `billing-pods`: fetch RunPod pod billing records through REST or `--backend runpodctl`.
- `billing-endpoints`: fetch RunPod Serverless endpoint billing records through REST or `--backend runpodctl`.
- `billing-network-volumes`: fetch RunPod network volume billing records through REST or `--backend runpodctl`.
- `cost-report`: estimate or fetch billing cost from a remote run record.
- `list-network-volumes`: list RunPod network volumes.
- `get-network-volume`: fetch one RunPod network volume.
- `list-templates`: list RunPod templates.
- `get-template`: fetch one RunPod template.

## Artifact Fetch And Verification

- `fetch-proxy-file`: fetch a file from a pod's RunPod HTTP proxy.
- `verify-proxy-packet`: download and close out an execution packet via HTTP proxy.
- `fetch-tcp-file`: fetch a file through a direct TCP HTTP service.
- `verify-tcp-packet`: download and close out an execution packet via direct TCP.
- `verify-network-volume-s3`: download a declared RunPod network-volume S3 archive, extract it locally, write verified egress status, and run normal artifact closeout.

HTTP proxy and direct TCP verification are for sanitized, short-lived smoke artifacts. Use durable egress for private or production artifacts.

## Linear API

- `linear-issue`: fetch a Linear issue body through the GraphQL API.
- `linear-comment`: post a Linear issue comment from a file.

Both commands require Linear API access. `linear-comment` mutates only with `--execute --yes-comment-linear`.
