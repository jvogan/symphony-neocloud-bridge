# RunPod Operations Notes

Read this when enabling or operating remote RunPod runs from Symphony workers.

For ambiguous pod create, boot, proxy, runtime-metrics, git-bootstrap, GPU scheduling, or artifact-fetch failures, use `failure-playbook.md` before relaunching the same workload.

## Tool Surface

The RunPod MCP server can manage pods, templates, endpoints, network volumes, and container registry auths when `RUNPOD_API_KEY` is injected into the worker runtime. The docs MCP server is separate and does not require auth.

Current MCP gaps to plan around:

- no direct pod log retrieval
- no direct in-pod exec
- no direct billing-history query through MCP. The bridge uses RunPod REST billing endpoints when `RUNPOD_API_KEY` is available, or `runpodctl billing ...` through `--backend runpodctl` when the operator host has `runpodctl`.
- no direct MCP SSH connection command fetch. `runpod-bridge pod-ssh-info <pod-id>` resolves this through `runpodctl ssh info` when `runpodctl` is installed.

Use startup-command workloads that write their own logs, heartbeat, status, artifacts, hashes, and egress status. The local bridge can call the RunPod REST pod lifecycle and billing APIs when `RUNPOD_API_KEY` is present and explicit execute flags are supplied. Prefer `runpod-bridge run-handoff` for worker-produced packets and `runpod-bridge run-remote` for orchestrator-owned manifests; both combine create, packet verification, and cleanup in one audit record. Use `runpod-bridge cost-report --fetch-billing` for closeout when available; otherwise mark cost as an estimate.

Record `billing.cost_center`, `billing.project_code`, and `billing.resource_owner` in manifests when known. Treat those fields as local closeout metadata until provider-side assignment is verified through RunPod's supported operator surface.

Rendered create payload size is a practical launch constraint. RunPod's public REST docs do not currently document a `dockerStartCmd` byte limit, but a live W1 smoke observed a create failure around a 65KB rendered startup body. `runpod-bridge preflight` reports `payload_post_body_bytes`, warns near 48KB, and blocks above the bridge hard limit. Compress large inline material with gzip and base64, or move it into a git snapshot, packet file, network volume, or object store before paid creation.

Remote git bootstrap is a pre-workload dependency. The bridge calls `git clone` before `startup.commands` runs, so the image or template must already contain `git`. Installing git inside `startup.commands` is too late. `runpod-bridge preflight` blocks paid git-source launches unless `runpod.image_capabilities` declares `git` or `startup.bootstrap.image_has_git` is true. Verify exact images with a tiny bootstrap canary before expensive science runs.

RunPod Flash and generic Serverless endpoints are now important RunPod surfaces. They use a different lifecycle from Pods. Until a Flash or Serverless adapter is implemented, use the pod runner only for pod manifests. For Flash, require local `flash build`/validation, guarded deploy authorization, endpoint job/output proof, endpoint billing, and `flash undeploy` or documented retention. Flashboot is a startup optimization for Serverless workers. Treat it as a startup signal. Workload success still requires declared artifact and validation proof.

Interruptible Pods are appropriate only for retryable, checkpointed workloads. Before paid launch, require a checkpoint policy, explicit resume or rerun policy, and durable artifact egress such as a network volume, RunPod network-volume S3, SCP, object-store upload, or presigned upload.

When `runpodctl` is available, `render-runpodctl-create` shows the equivalent pod creation command and includes `--terminate-after` from `budget.terminate_after_minutes`. The REST create path records that value for audit and runtime awareness, but the platform-side stop/delete backstop is exposed only through `runpodctl pod create`.

## Efficient Monitoring

- Poll `get_pod` with machine and network-volume details every 30 seconds by default.
- Record `desiredStatus`, `lastStartedAt`, `lastStatusChange`, `costPerHr`, `adjustedCostPerHr`, `machine.dataCenterId`, `machine.gpuTypeId`, `publicIp`, `portMappings`, and `networkVolume.id` when present.
- Treat pod `RUNNING` as resource readiness only. Workload progress requires heartbeat, status files, or service health checks.
- For HTTP proxy smokes, probe `https://<pod-id>-<internal-port>.proxy.runpod.net/` and declared status/artifact paths directly. REST `publicIp` and `portMappings` can lag a working HTTP proxy, so empty REST fields by themselves do not block proxy readiness.
- Interpret proxy 404s only against declared `/http` ports and expected paths. A 404 on an undeclared port or a wrong path is not diagnostic. Repeated 404s on declared status or artifact paths after GraphQL runtime appears mean the workload HTTP service did not reach that path.
- Treat negative GraphQL `runtime.uptimeInSeconds` as invalid provider telemetry or pod-agent trouble. Require a workload heartbeat, SSH or log tail, or fetched artifact packet before continuing spend.
- Treat `startup.inspection.http_artifact_server_port` as completion-only. A connection refusal before `inspection_hold` usually means the workload has not finished. For live progress, use `startup.progress.http_status_server_port` or SSH and log tail.
- Fail closed on silence timeout and continue cleanup.

## Ports And SSH

- Exposed HTTP proxy URLs are public and have proxy timeout limits. Use authentication and short status endpoints.
- `runpod-bridge verify-proxy-packet` can fetch sanitized smoke artifacts through an exposed HTTP proxy. Production egress should rely on durable channels.
- `runpod-bridge verify-tcp-packet` fetches the same packet through direct TCP when RunPod exposes a public TCP mapping.
- `runpod-bridge run-remote --verification-mode auto` tries direct TCP first, then falls back to HTTP proxy.
- `runpod-bridge run-handoff` uses the verification and cleanup defaults recorded in `provider_handoff.json`.
- HTTP proxy 401/403 responses should fail fast and trigger cleanup rather than waiting out the inspection window.
- Direct TCP is required for full SSH/SCP and long-running streams.
- Full SSH/SCP requires public IP support, `22/tcp`, a registered SSH public key, and an SSH daemon inside the pod image or template.
- Basic proxied SSH gives shell access only. SCP and SFTP need direct TCP.

## Volumes And Cleanup

- Pod volume data may persist across restarts but stays tied to the pod.
- Network volumes are the right option for large retained artifacts or reuse across pods.
- RunPod network-volume S3 is the preferred durable file-management path when a network volume is retained.
- `runpod_network_volume_s3` is the explicit bridge egress mode for post-cleanup artifact pull from a retained RunPod network volume. It requires a network volume ID, a datacenter-specific endpoint or data center ID, and separate RunPod S3 API credentials injected as AWS-compatible env vars.
- `aws_s3_presigned_upload` is the preferred AWS S3 path when the pod should not receive AWS credentials. The orchestrator injects short-lived PUT URLs at runtime. Never store those URLs in manifests or Linear.
- `object_store_upload` uploads the declared archive and hash file when `RUNPOD_OBJECT_STORE_URI`, AWS-compatible credentials, and the AWS CLI are present in the runtime.
- If a network volume is attached, closeout deletes the pod and retains the volume only when retention is authorized and documented.
- For Serverless endpoints, use endpoint billing rather than pod billing. For retained network volumes, use network-volume billing as part of closeout.

## Worker Coordination

- Use one mutating owner per run.
- Read-only monitors can poll and comment but cannot stop, delete, or update resources.
- Lock through Linear by recording `run_id`, worker ID, pod name, pod ID, and cleanup owner.
- Before creating a pod, list by expected name and stop if an active pod already exists.
- The local REST adapter blocks active duplicate pod prefixes by default. `run-remote` and `run-handoff` acquire an atomic local launch lock before paid creation. Override only when the Linear issue explicitly approves parallel pods.
