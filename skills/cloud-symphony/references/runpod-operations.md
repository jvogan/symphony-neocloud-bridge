# RunPod Operations Notes

Read this when enabling or operating remote RunPod runs from Symphony workers.

For ambiguous pod create, boot, proxy, runtime-metrics, git-bootstrap, GPU scheduling, or artifact-fetch failures, use `failure-playbook.md` before relaunching the same workload.

## Tool Surface

The RunPod MCP server can manage pods, templates, endpoints, network volumes, and container registry auths when `RUNPOD_API_KEY` is injected into the worker runtime. The docs MCP server is separate and does not require auth.

Current MCP gaps to plan around:

- no direct pod log retrieval
- no direct in-pod exec
- no direct billing-history query through MCP; the bridge uses RunPod REST billing endpoints when `RUNPOD_API_KEY` is available, or `runpodctl billing ...` through `--backend runpodctl` when the operator host has `runpodctl`
- no direct MCP SSH connection command fetch; `cloud-bridge pod-ssh-info <pod-id>` resolves this through `runpodctl ssh info` when `runpodctl` is installed

Use startup-command workloads that write their own logs, heartbeat, status, artifacts, hashes, and egress status. The local bridge can call the RunPod REST pod lifecycle and billing APIs when `RUNPOD_API_KEY` is present and explicit execute flags are supplied. Prefer `cloud-bridge run-handoff` for worker-produced packets and `cloud-bridge run-remote` for orchestrator-owned manifests because they combine create, packet verification, and cleanup in one audit record. Use `cloud-bridge cost-report --fetch-billing` for closeout when available; otherwise mark cost as an estimate.

Record `billing.cost_center`, `billing.project_code`, and `billing.resource_owner` in manifests when known. Treat those fields as local closeout metadata until provider-side assignment is verified through RunPod's supported operator surface.

Rendered create payload size is a practical launch constraint. RunPod's public REST docs do not currently document a `dockerStartCmd` byte limit, but a live W1 smoke observed a create failure around a 65KB rendered startup body. `cloud-bridge preflight` reports `payload_post_body_bytes`, warns near 48KB, and blocks above the bridge hard limit. Compress large inline material with gzip/base64 or move it into a git snapshot, packet file, network volume, or object store before paid creation.

Remote git bootstrap is a pre-workload dependency. The bridge calls `git clone` before `startup.commands`, so the image or template must already contain `git`; installing git inside `startup.commands` is too late. `cloud-bridge preflight` blocks paid git-source launches unless `runpod.image_capabilities` declares `git` or `startup.bootstrap.image_has_git` is true. Verify exact images with a tiny bootstrap canary before expensive science runs.

RunPod Flash and generic Serverless endpoints are now important RunPod surfaces, but they are not the same lifecycle as Pods. Until a Flash or Serverless provider path has automated launch support, use the pod runner only for pod manifests. For Flash, require local `flash build`/validation, guarded deploy authorization, endpoint job/output proof, endpoint billing, and `flash undeploy` or documented retention. Flashboot is a startup optimization for Serverless workers, not a success signal.

Interruptible Pods are appropriate only for retryable, checkpointed workloads. Before paid launch, require a checkpoint policy, explicit resume or rerun policy, and durable artifact egress such as a network volume, RunPod network-volume S3, SCP, object-store upload, or presigned upload.

When `runpodctl` is available, `render-runpodctl-create` shows the equivalent pod creation command and includes `--terminate-after` from `budget.terminate_after_minutes`. The REST create path records that value for audit and runtime awareness, but official docs expose the platform-side stop/delete backstop through `runpodctl pod create`.

## Efficient Monitoring

- Poll `get_pod` with machine and network-volume details every 30 seconds by default.
- Record `desiredStatus`, `lastStartedAt`, `lastStatusChange`, `costPerHr`, `adjustedCostPerHr`, `machine.dataCenterId`, `machine.gpuTypeId`, `publicIp`, `portMappings`, and `networkVolume.id` when present.
- Treat pod `RUNNING` as resource readiness only. Require workload heartbeat/status files or service health checks before claiming progress.
- For HTTP proxy smokes, directly probe `https://<pod-id>-<internal-port>.proxy.runpod.net/` and declared status/artifact paths. REST `publicIp` and `portMappings` can lag a working HTTP proxy, so empty REST fields are not by themselves a proxy-readiness blocker.
- Interpret proxy 404s only for declared `/http` ports and expected paths. A 404 on an undeclared port or a wrong path is not diagnostic; repeated 404s on the declared status/artifact paths after GraphQL runtime appears mean the workload HTTP service did not reach that path.
- Treat negative GraphQL `runtime.uptimeInSeconds` as invalid provider telemetry or pod-agent trouble. It is neither productivity nor a normal success signal; require a workload heartbeat, SSH/log tail, or fetched artifact packet before continuing spend.
- Treat `startup.inspection.http_artifact_server_port` as completion-only. A connection refusal before `inspection_hold` usually means the workload has not finished, not that it is productive. Use `startup.progress.http_status_server_port` or SSH/log tail for live progress.
- Fail closed on silence timeout and continue cleanup.

## Ports And SSH

- Exposed HTTP proxy URLs are public and have proxy timeout limits; use authentication and short status endpoints.
- `cloud-bridge verify-proxy-packet` can fetch sanitized smoke artifacts through an exposed HTTP proxy, but do not rely on it as the only production egress path.
- `cloud-bridge verify-tcp-packet` can fetch the same packet through direct TCP when RunPod exposes a public TCP mapping.
- `cloud-bridge run-remote --verification-mode auto` tries direct TCP first, then falls back to HTTP proxy.
- `cloud-bridge run-handoff` uses the verification and cleanup defaults recorded in `provider_handoff.json`.
- HTTP proxy 401/403 responses should fail fast and trigger cleanup rather than waiting out the whole inspection window.
- Direct TCP is required for full SSH/SCP and long-running streams.
- Full SSH/SCP requires public IP support, `22/tcp`, a registered SSH public key, and an SSH daemon inside the pod image/template.
- Basic proxied SSH is useful for shell access but does not support SCP/SFTP.

## Volumes And Cleanup

- Pod volume data may persist across restarts but is tied to the pod.
- Network volumes are the right option for large retained artifacts or reuse across pods.
- RunPod network-volume S3 is the preferred durable file-management path when a network volume is retained.
- `runpod_network_volume_s3` is the explicit bridge egress mode for artifact pull from a retained RunPod network volume. It requires a network volume ID, datacenter-specific endpoint or data center ID, and separate RunPod S3 API credentials injected as AWS-compatible env vars. The pod runner should wait for the declared archive before cleanup, then verify cleanup separately.
- `aws_s3_presigned_upload` is the preferred AWS S3 path when the pod should upload a workspace archive without AWS credentials. The orchestrator injects short-lived PUT URLs at runtime; never store those URLs in manifests or Linear. Treat pod-side `uploaded` status as upload-submitted evidence only; final success requires trusted object/hash verification and `egress_status: verified`.
- `object_store_upload` can upload the declared archive and hash file when `RUNPOD_OBJECT_STORE_URI`, AWS-compatible credentials, and the AWS CLI are present in the runtime. Treat pod-side `uploaded` status as upload-submitted evidence only; final success requires trusted object/hash verification and `egress_status: verified`.
- If a network volume is attached, closeout should delete the pod and retain the volume only when that retention is authorized and documented.
- For Serverless endpoints, use endpoint billing rather than pod billing. For retained network volumes, use network-volume billing as part of closeout.

## Worker Coordination

- Use one mutating owner per run.
- Let read-only monitors poll and comment, but do not let them stop/delete/update resources.
- Lock through Linear by recording `run_id`, worker ID, pod name, pod ID, and cleanup owner.
- Before creating a pod, list by expected name and stop if an active pod already exists.
- The local REST adapter blocks active duplicate pod prefixes by default, and `run-remote`/`run-handoff` acquire an atomic local launch lock before paid creation. Override only when the Linear issue explicitly approves parallel pods.
