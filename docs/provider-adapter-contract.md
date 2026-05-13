# Provider Adapter Contract

The bridge should keep domain workloads provider-neutral. RunPod is the first adapter, but the manifest separates common execution needs from provider-specific resource fields so other neoclouds can reuse the same lifecycle later.

## Common Contract

Every provider adapter must support or explicitly reject:

- manifest validation without resource creation
- contract self-check without resource creation
- startup script or equivalent command rendering
- exact source checkout or mounted snapshot
- resource state polling
- heartbeat/status/log/artifact capture
- SHA-256 artifact reporting
- archive packet creation when workspace archive egress is declared
- durable artifact egress planning and proof
- budget and runtime limits
- billing or estimated cost reporting
- cleanup or retention closeout
- parseable `symphony-outcome`

## RunPod REST Adapter

The local `runpod-bridge` CLI includes a stdlib REST adapter for the RunPod pod lifecycle:

- `write-handoff` and `validate-handoff` record the worker-to-orchestrator boundary without paid resource creation.
- `run-handoff` consumes `provider_handoff.json`, acquires the launch lock, and delegates to the guarded remote runner.
- `run-remote` acquires the launch lock, creates, verifies, and cleans up one authorized RunPod run in a single audit record.
- `create-pod` writes an audited request and can call `POST /pods` only after all launch gates pass.
- `list-pods` and `get-pod` provide read-only resource inspection for monitoring workers.
- `runtime-metrics` queries RunPod GraphQL `pod.runtime` fields to detect crash loops or recent restarts that REST pod state cannot distinguish.
- `productivity-plan` distinguishes live progress channels from completion-only artifact inspection so workers do not overclaim from pod `RUNNING` or a refused artifact port.
- `cleanup-pod` writes an audited stop/delete request and can call `POST /pods/{podId}/stop` or `DELETE /pods/{podId}` only with explicit confirmation.
- `fetch-proxy-file`, `verify-proxy-packet`, `fetch-tcp-file`, and `verify-tcp-packet` can inspect sanitized artifacts through exposed HTTP proxy or TCP ports when the manifest declares matching ports.
- `contract-self-check`, `preflight`, `profiles`, and `provider-capabilities` expose launch intelligence before any paid work.
- `egress-plan` explains durable artifact movement requirements for workspace archive, network volume, SCP, presigned upload, and object-store modes.
- `aws-orchestrator-plan` renders optional AWS companion commands for STS-scoped object-store upload, RunPod network-volume S3 tooling, ECR registry refresh, Secrets Manager refs, SQS handoffs, DynamoDB locks, and EventBridge cleanup backstops without executing them.
- `billing-pods` and `cost-report` use the REST billing surface when available, with runtime x cost fields as fallback.
- `billing-pods`, `billing-endpoints`, and `billing-network-volumes` can use `--backend runpodctl` for read-only billing checks when the operator host has `runpodctl`.
- Optional `billing.cost_center`, `billing.project_code`, and `billing.resource_owner` manifest fields preserve local attribution even when provider-side cost-center assignment remains operator-managed.
- `render-runpodctl-create` renders `budget.terminate_after_minutes` to `runpodctl pod create --terminate-after`; the REST create path records the value but does not enforce it platform-side.
- `orchestrator-scan`, `orchestrator-once`, and `issue-intake` make worker handoff packets executable by a trusted orchestrator lane.
- `dashboard`, `supervise`, and `recover-run` support multi-run monitoring and failure cleanup.

The adapter still depends on workload-written logs, heartbeats, status files, and artifacts because direct pod log streaming and in-pod exec are not available through the observed MCP surface.

HTTP proxy and direct TCP verification are deliberately treated as non-authoritative for private or production workloads. Durable artifact proof should use the declared workspace archive plus SCP, network volume, RunPod network-volume S3, AWS S3 presigned upload, or object-store upload.

## Candidate RunPod Adapters

The current mutating adapter is `runpod_pod_v1`. New RunPod surfaces should become separate adapters so their lifecycle, monitoring, and cleanup semantics stay explicit.

### `runpod_flash_v1`

Use for Python-native GPU/CPU functions and apps built with RunPod Flash. This adapter should validate `flash` app metadata, endpoint names, function entrypoints, GPU/CPU settings, worker bounds, timeouts, dependencies, environment variable policy, volume policy, and undeploy/retention policy. Local readiness should run `flash build` or an equivalent no-deploy validation before any paid deploy.

Success must come from endpoint job results, declared outputs, fetched artifacts, validation commands, and billing/cleanup evidence. A deployed environment, endpoint URL, or worker state is not enough.

### `runpod_serverless_v1`

Use for template, Hub, custom-container, or existing-ID Serverless endpoints. This adapter should own endpoint list/get/create/update/delete, `/run` or `/runsync` job submission, status polling, result validation, `/billing/endpoints` closeout, and endpoint deletion or documented retention.

Flashboot belongs here as a startup optimization, not as a success signal.

### `runpod_cluster_v1`

Use later for Instant Clusters and managed multi-node jobs. This adapter needs scheduler-aware contracts: node count, rank/world-size setup, rendezvous, Slurm or framework commands, checkpoint cadence, silence timeout, multi-node artifact aggregation, cost cap, and teardown proof.

## Official RunPod Surfaces

The bridge tracks these official surfaces:

- Pod REST API: `POST /pods`, `GET /pods`, `GET /pods/{podId}`, pod update/start/stop/delete/reset/restart.
- Pod GraphQL runtime metrics: `pod.runtime.uptimeInSeconds`, container CPU/memory samples, GPU utilization samples, and port mappings.
- Billing REST API: `GET /billing/pods`, `GET /billing/endpoints`, `GET /billing/networkvolumes`.
- Cost centers: provider-side attribution for Pods, Serverless endpoints, network volumes, and Instant Clusters; bridge manifests record intended attribution for closeout and reconciliation.
- Network volumes: create/list/get/update/delete; Pods attach volumes at deployment time and preserve data after pod deletion.
- RunPod network-volume S3: post-cleanup artifact pull through datacenter-specific S3-compatible endpoints with separate S3 API credentials.
- AWS S3 presigned upload: direct archive egress to AWS S3 with no AWS credentials inside the pod.
- Templates: create/list/get/update/delete.
- Flash: Python `@Endpoint` functions, Flash apps/environments, Flash CLI, Serverless endpoints, network volumes, and Flashboot startup optimization.
- RunPod CLI: `runpodctl` can manage Pods, Serverless endpoints, templates, network volumes, billing, SSH, and peer-to-peer file transfer.
- Agent integrations: RunPod publishes agent skills and MCP servers, but this bridge remains the policy layer for launch gates and closeout.
- Instant Clusters: managed multi-node compute for distributed workloads and future adapter work.
- Ports: HTTP proxy uses the pod/port proxy host; TCP uses `publicIp` plus `portMappings`; symmetric TCP uses pseudo ports above `70000`.
- SSH/SCP: full SCP requires public IP support, `22/tcp`, sshd in the image, and SSH public key auth.
- Interruptible Pods: allowed only with explicit checkpoint/resume policy and durable egress before paid launch.

## Provider-Specific Block

Provider details live under a named block such as `runpod`. Future adapters should add their own block rather than changing domain workload fields.

```json
{
  "provider": {
    "name": "runpod",
    "adapter": "runpod_pod_v1"
  },
  "runpod": {
    "imageName": "python:3.12-slim",
    "gpuCount": 0
  }
}
```

## Workload Scale

- `small`: CPU or short GPU smoke, single command, small artifacts.
- `medium`: one GPU or longer run with normal artifact capture.
- `large`: multi-hour, higher cost, volume or larger artifact egress.
- `huge`: long-running or sharded work requiring checkpointing, explicit egress, and stricter monitoring.

Huge workloads should declare checkpoint policy and avoid relying only on ephemeral workspace archives.

## Inline Smoke

`repo.source: inline_commands` is allowed only for small smoke workloads. Production workloads should use an immutable git source or mounted snapshot so the run can be reproduced outside the manifest.
