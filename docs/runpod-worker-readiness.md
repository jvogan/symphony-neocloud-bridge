# RunPod Worker Readiness

Use this checklist before letting Symphony workers launch or monitor RunPod resources.

## Current Gaps

- No direct RunPod MCP tool for pod container logs or in-pod command execution.
- RunPod MCP still does not provide pod billing history, but `runpodctl billing` and REST billing both do. Use `billing-pods --backend runpodctl` when `runpodctl` is installed, REST billing when `RUNPOD_API_KEY` is available, or runtime x cost as the fallback.
- The SSH connection command gap is resolved when `runpodctl` is installed: use `cloud-bridge pod-ssh-info <pod-id>` as a wrapper around `runpodctl ssh info`.
- RunPod GraphQL exposes read-only pod runtime metrics and GPU catalog availability. Use `runtime-metrics` to catch crash loops that REST `get-pod` cannot distinguish from slow work, and `gpu-catalog` to distinguish GPU/DC catalog mismatch from zero current capacity before retrying `POST /pods`. The bridge sends a browser-style User-Agent by default; override `RUNPOD_GRAPHQL_USER_AGENT` only if the operator environment rewrites or blocks it.
- No local `runpodctl` fallback is installed in this workspace unless `doctor` reports it.
- No local `flash` CLI is installed in this workspace unless `doctor` reports it. Flash is a future Serverless/Python adapter lane, not part of the current pod runner.
- Symphony Codex worker shell sandboxes may not have outbound DNS/TCP even when environment variables are injected. Treat worker-side `create-pod`, `get-pod`, and packet verification as available only after a real network preflight proves shell networking works.
- The committed bridge CLI now covers local `doctor`, `audit-manifests`, `validate-manifest`, `contract-self-check`, `preflight`, `profiles`, `provider-capabilities`, `linear-issue`, `linear-comment`, `render-startup`, `render-runpodctl-create`, `plan`, `prepare`, `source-ingress-plan`, `write-handoff`, `validate-handoff`, `issue-intake`, `run-local`, `monitor`, `supervise`, `dashboard`, `closeout`, and `remote-outcome`.
- The committed bridge CLI also includes guarded RunPod REST/GraphQL operations: `run-handoff`, `run-remote`, `orchestrator-scan`, `orchestrator-once`, `create-pod`, `list-pods`, `get-pod`, `gpu-catalog`, `runtime-metrics`, `billing-pods`, `billing-endpoints`, `billing-network-volumes`, `cost-report`, `list-network-volumes`, `get-network-volume`, `list-templates`, `get-template`, `recover-run`, and `cleanup-pod`.
- Optional `runpodctl` integration covers read-only SSH info, billing fallback, and non-mutating pod create command rendering with `--terminate-after`.
- Remote mutation remains blocked unless the manifest passes launch gates, records `launch_authorization`, and the caller supplies the explicit paid-resource or cleanup confirmation flag.
- `run-remote` and `run-handoff` acquire an atomic local launch lock keyed by resource prefix before paid creation. Use `RUNPOD_BRIDGE_LOCK_DIR` or `--lock-dir` when multiple orchestrators share a host or mounted lock directory.
- Workspace archive egress, generic object-store archive upload, and RunPod network-volume S3 planning are supported by the bridge. Direct SCP transfer remains adapter work.
- HTTP proxy and direct TCP packet fetching are supported for sanitized smoke inspection, but they are not reliable enough to be the only production artifact channel.

## Enable For Symphony Workers

- RunPod API access configured outside the repo. If the worker shell can resolve and connect to RunPod REST, inject `RUNPOD_API_KEY` with an audited `shell_environment_policy.include_only` entry. If not, use the worker only for local validation and hand the prepared launch packet to a trusted orchestrator or `after_run` hook that runs outside the Codex sandbox.
- RunPod docs MCP server enabled for documentation lookup; it does not require auth.
- Linear access for issue intake, lock/status updates, and closeout comments. `linear-comment` mutates Linear only with `--execute --yes-comment-linear`.
- Git access to the exact repo source or a clean snapshot path.
- A repo-local `cloud-bridge` CLI or equivalent scripts for manifest validation, startup rendering, dry-run planning, pod monitoring, artifact hashing, and closeout.
- Run `cloud-bridge doctor` before handing a RunPod issue to Symphony. Warnings are acceptable for local dry-runs, but failures mean the worker will probably not discover the bridge.
- Use `cloud-bridge audit-manifests <repo-or-template-dir>` when onboarding a domain repo. This catches stale copied launch bundles and old field names before workers copy a broken template.
- Use `cloud-bridge prepare` to emit `launch_manifest.json`, `startup.sh`, `local_preflight.json`, and `provider_handoff.json` before any paid launch.
- Use `cloud-bridge source-ingress-plan` before private-source launches that use prepared snapshots, archive URL refs, or RunPod network-volume S3 staging. When a local archive path is provided, the plan checks the source archive SHA before upload.
- Use `cloud-bridge contract-self-check`, `cloud-bridge source-check --execute` or `source-ingress-plan`, `cloud-bridge preflight`, and `cloud-bridge egress-plan` before assigning large or huge workloads.
- Use `cloud-bridge validate-handoff` on `provider_handoff.json` before passing it to an orchestrator.
- Use `cloud-bridge issue-intake` when the work starts from a Linear issue body and manifest pair.
- Use `cloud-bridge run-remote` for orchestrator-side paid smokes so create, packet verification, and cleanup land in one audit record.
- Use `cloud-bridge run-handoff` when the orchestrator is consuming a worker-produced packet.
- Use `cloud-bridge orchestrator-scan` or `cloud-bridge orchestrator-once` for a local handoff queue.
- Use `cloud-bridge create-pod` without `--execute` when you need to review the exact REST request before any paid launch.
- For Symphony workers, run a cheap network preflight before assigning remote mutation: `cloud-bridge list-pods --name-prefix definitely-no-match --json`. If it fails with DNS or connection errors, do not retry inside the worker; have the worker close out with the prepared packet and let an orchestrator-side lane run `run-handoff` and Linear finalization.
- For GPU manifests with explicit `gpuTypeIds`, run `cloud-bridge gpu-catalog --manifest <manifest> --json` before any paid create retry loop. A catalog mismatch should change the manifest; capacity-zero can be retried or widened.
- Use `cloud-bridge validate-linear-issue` on repo-provided issue bodies before handing them to Symphony.
- Use `cloud-bridge productivity-plan` before long remote launches to confirm the manifest has a live progress channel. Use `cloud-bridge supervise` for long-running local packets and `cloud-bridge dashboard` for multi-run operator views.
- Use `cloud-bridge runtime-metrics <pod-id> --expected-elapsed-minutes <minutes> --json` shortly after allocation and again a few minutes later. If `uptimeInSeconds` resets or stays near zero after long elapsed time, treat it as a crash loop and clean up unless an operator can inspect logs immediately.
- Use `cloud-bridge cost-report --fetch-billing` after remote cleanup when the RunPod billing API is reachable.
- Use `cloud-bridge billing-endpoints` and `cloud-bridge billing-network-volumes` for non-pod cost closeout when Serverless endpoints or retained volumes are involved.
- Use `cloud-bridge billing-pods --backend runpodctl`, `billing-endpoints --backend runpodctl`, or `billing-network-volumes --backend runpodctl` when the operator host has `runpodctl` configured.
- Use `cloud-bridge render-runpodctl-create <manifest>` to inspect the equivalent `runpodctl pod create` command and confirm both `--docker-args` startup execution and `--terminate-after` are present when platform-side deletion backstop is required.
- Use `cloud-bridge pod-ssh-info <pod-id>` to fetch SSH details through `runpodctl ssh info`; do not paste private key material or one-time transfer codes into Linear.
- Use `cloud-bridge recover-run` on failed or interrupted run records before manually inspecting the console.
- Use `cloud-bridge verify-proxy-packet` or `cloud-bridge verify-tcp-packet` only for short-lived, sanitized smokes.
- Optional `flash` CLI for future RunPod Flash app/function validation. Do not let Flash deploys bypass `remote_launch_allowed`, budget, artifact proof, and undeploy policy.
- Optional `runpodctl` for CLI fallback, endpoint inspection, SSH key management, billing reads, `--terminate-after` pod creation, and operator-assisted file transfer. Current official CLI docs expose `runpodctl ssh info`, not a documented generic pod exec channel; installing `runpodctl` helps only if SSH can be established.
- Optional SSH public key registered in RunPod if workers need SSH. Full SSH/SCP also requires a public IP-capable pod, exposed `22/tcp`, and an SSH daemon inside the image/template.
- Optional object-store or network-volume policy for large artifact egress. Do not put storage credentials in manifests or Linear.

## Monitoring Contract

Use three monitoring layers:

1. Resource monitor: poll RunPod pod state with machine and volume details.
2. Runtime monitor: poll RunPod GraphQL runtime metrics for container uptime and point-in-time utilization.
3. Workload monitor: require files written by the startup workload.
4. Peek channel: use live `/healthz` progress HTTP for sanitized smokes, or SSH/log tail for private long-running workloads.

Treat provider state as intent, not proof. `RUNNING`, pod start, worker exit, and command return code do not establish success unless the workload status file, logs, artifact hashes, and declared artifacts agree.

Poll fields to record when available:

- pod ID, desired status, last start time, and last status change
- image/template, GPU type/count, machine ID, data center, and volume ID
- `costPerHr` or `adjustedCostPerHr`
- public IP and port mappings if ports are exposed
- GraphQL `runtime.uptimeInSeconds`, `runtime.container.cpuPercent`, `runtime.container.memoryPercent`, and GPU utilization fields

Runtime metrics are mainly negative proof. A tiny uptime after 30 minutes of elapsed billing means the current container is new, not that a long workload is progressing. Non-zero CPU or GPU utilization in one sample is not enough to claim productivity.

Monitor loops should call `cloud-bridge progress-report <manifest> <pod-id> --previous <prior.json> --out <next.json>` and report `classification.state` verbatim, with `workload_progressing`, `monitor_alive`, `outage_suspected`, and `next_action` copied exactly.

Workload files:

- `runpod-execution/monitor_events.ndjson` for heartbeats and phase changes
- `runpod-execution/status.json` for final status, command exit code, and validation summary
- `runpod-execution/logs/startup.log` for stdout/stderr captured by the startup command
- `runpod-execution/artifact_hashes.jsonl` for runtime SHA-256 hash ledger entries, plus `runpod-execution/artifact_hashes.json` when local closeout writes the normalized summary

Use a default 30 second poll interval and a 10 minute silence timeout unless the manifest overrides them.

Live productivity means one of these is fresh and advancing: `/healthz` from `startup.progress.http_status_server_port`, an SSH tail of `startup.log`, or a fetched status/heartbeat packet. A refused TCP connection to `startup.inspection.http_artifact_server_port` is expected while the workload is still running because the artifact server starts only after `inspection_hold`; do not treat that as either failure or productivity proof. See `docs/runpod-observability-ladder.md` for the canonical rung model.

## Execution Modes

- Startup command is the default automation path. It must tee stdout/stderr into the declared log file and write heartbeats.
- Paid startup must have `workload.stage_contract`: real inputs, exact workload commands, route proof for input materialization/tool invocation/artifact validation, expected outputs, done markers, timeout, resume policy, fail-closed behavior, and claim level.
- Remote startup now supports guarded git bootstrap when `RUNPOD_ENABLE_REPO_BOOTSTRAP=1`; actual remote launch requires an immutable git SHA or snapshot/archive digest reference.
- Git bootstrap requires `git` in the image before `startup.commands` run. Exact images should be canaried and declared with `runpod.image_capabilities: ["git"]`; otherwise use inline, prepared_snapshot, or object-store bootstrap.
- In Codex/Symphony deployments where shell networking is blocked, split execution into two lanes: the sandboxed worker performs issue intake, manifest validation, `prepare`, `validate-handoff`, and `run-local`; the unsandboxed orchestrator or trusted `after_run` hook performs `run-handoff` and the final Linear `symphony-outcome` update. Use the lower-level `create-pod`, `get-pod`, packet verification, and `cleanup-pod` commands only when troubleshooting.
- Flash execution should become its own mode after adapter support lands: local `flash build`/validation first, guarded deploy second, endpoint job/output proof third, undeploy or documented retention last.
- When `artifact_egress.mode` is `workspace_archive`, startup writes the declared archive path with logs, status, heartbeat, hash, and artifact entries.
- Local dry-run should use `cloud-bridge run-local` first; it executes the rendered startup contract with `RUNPOD_REPO_DIR` pointed at a local repo directory and produces the same execution packet shape.
- SSH is a recovery/interactive path, not the default success path.
- Full SSH/SCP is required for direct file transfer. Basic proxied SSH does not provide SCP/SFTP.
- HTTP proxy is suitable for short web requests and status endpoints, but exposed services are public and must authenticate.
- A simple HTTP artifact server can be useful for smoke inspection. Proxy failures such as HTTP 401/403 or unresolved TCP port mappings must fail fast and still trigger cleanup. This artifact server is a completion signal only; configure `startup.progress.http_status_server_port` when agents need live workload visibility before completion.
- TCP exposure is better for long-running streams or SSH, but port mappings can change after resets.

## Artifact Egress

Prefer one of these explicit modes:

- `workspace_archive`: create a tarball inside `runpod-execution/artifacts/` and validate hashes.
- `network_volume`: write artifacts to an attached network volume and record the volume ID.
- `runpod_network_volume_s3`: write artifacts to an attached network volume, poll the declared archive through RunPod's S3-compatible API, verify locally, then delete or stop the pod with cleanup verification.
- `scp`: copy artifacts over full SSH/SCP after the pod is ready.
- `aws_s3_presigned_upload`: upload the archive with a runtime-injected AWS S3 PUT URL so the pod does not receive AWS credentials.
- `object_store_upload`: upload to an approved bucket using runtime-injected credentials.

For `aws_s3_presigned_upload`, the trusted orchestrator creates short-lived presigned PUT URLs for the archive and optional hash ledger, injects them through `RUNPOD_PRESIGNED_ARCHIVE_PUT_URL` and `RUNPOD_PRESIGNED_HASH_PUT_URL` or the declared env refs, and never writes those URLs to the manifest, repo, or Linear. If `requires_presigned_upload` is true, missing URLs or upload tooling fail the workload. Pod-side `egress_status: uploaded` only proves the upload command returned success; final closeout requires orchestrator-side object existence/download/hash verification and `egress_status: verified`.

For `object_store_upload`, the startup script can upload the declared archive and hash file when `RUNPOD_OBJECT_STORE_URI`, an AWS-compatible CLI, and runtime credentials are present. If `requires_object_store_upload` is true, missing upload configuration fails the workload. Pod-side `egress_status: uploaded` is upload-submitted evidence only; final closeout requires a trusted orchestrator to verify the object and hash, then record `egress_status: verified`.

Network-volume closeout is special: pod termination preserves data on the network volume, while stop semantics may not be available. Record the retained volume ID and cleanup owner. For `runpod_network_volume_s3`, use the volume ID as the bucket, inject the datacenter-specific endpoint such as `https://s3api-us-ks-2.runpod.io/`, and inject separate RunPod S3 API credentials through `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Avoid recursive listing as the primary proof for directories with many files or more than about 10GB of data.

When AWS is used as the orchestrator companion, run `aws-orchestrator-plan` from the trusted operator host. It renders STS, ECR, Secrets Manager, SQS, DynamoDB, and EventBridge command templates; it does not replace manifest validation, launch locks, artifact proof, or cleanup closeout.

## Runtime And Secret Backstops

- `budget.max_estimated_cost_usd` is still a bridge-side soft cap, not a provider kill switch.
- `budget.terminate_after_minutes` renders to `runpodctl pod create --terminate-after` for a platform-side deletion backstop when an operator uses the `runpodctl` create path. The REST create path records the value in runtime env but does not enforce it platform-side.
- Use restricted or read-only RunPod API keys where possible. The current official docs show console-managed create/edit/disable/delete flows; do not assume the bridge can safely mint and revoke per-run keys until an official API or CLI support is verified.
- Cost centers should be assigned in the RunPod console or future API/CLI support as part of operator closeout. Until then, keep Linear issue IDs in pod names and bridge records for spend reconciliation.

## Worker Coordination

- One mutating worker owns launch, update, stop/delete, and closeout for a run.
- Read-only monitor workers may poll state and report progress, but they must not mutate RunPod resources.
- Use the Linear issue as the human-readable lock source: record `run_id`, worker ID, pod name, pod ID, cleanup owner, and local launch-lock path.
- Use the bridge launch lock as the mechanical same-host guard. If separate orchestrator hosts are used, point `RUNPOD_BRIDGE_LOCK_DIR` at a shared mounted directory or rely on a Linear/queue lock before invoking `run-handoff`.
- Name resources with a deterministic prefix such as `symphony-<issue-id>-<run-id>`.
- Before launch, list pods by expected name and fail closed if an active pod already exists for the issue.
- On timeout or failure, close out with cleanup status and artifact state rather than leaving the issue ambiguous.

## Source Notes

- RunPod MCP docs: https://docs.runpod.io/get-started/mcp-servers
- RunPod agent skills: https://docs.runpod.io/get-started/agent-skills
- RunPod Flash overview: https://docs.runpod.io/flash/overview
- RunPod Flash endpoint parameters: https://docs.runpod.io/flash/configuration/parameters
- RunPod CLI overview: https://docs.runpod.io/runpodctl/overview
- RunPod CLI pods: https://docs.runpod.io/runpodctl/reference/runpodctl-pod
- RunPod CLI billing: https://docs.runpod.io/runpodctl/reference/runpodctl-billing
- RunPod CLI SSH: https://docs.runpod.io/runpodctl/reference/runpodctl-ssh
- Pod management and logs overview: https://docs.runpod.io/pods/manage-pods
- SSH requirements: https://docs.runpod.io/pods/configuration/use-ssh
- Port exposure behavior: https://docs.runpod.io/pods/configuration/expose-ports
- REST API overview and billing: https://docs.runpod.io/api-reference/overview
- Pod billing API: https://docs.runpod.io/api-reference/billing/GET/billing/pods
- Serverless billing API: https://docs.runpod.io/api-reference/billing/GET/billing/endpoints
- Network volume billing API: https://docs.runpod.io/api-reference/billing/GET/billing/networkvolumes
- RunPod network-volume S3 API: https://docs.runpod.io/storage/s3-api
- RunPod API keys: https://docs.runpod.io/get-started/api-keys
- RunPod cost centers: https://docs.runpod.io/accounts-billing/cost-centers
- Linear GraphQL API: https://linear.app/developers/graphql
