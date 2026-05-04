# RunPod Superpowers Watchlist

Checked against official RunPod docs and blog posts on 2026-05-01.

## Executive Summary

RunPod Flash is the biggest new bridge opportunity. RunPod announced Flash GA on 2026-04-30 as a Python SDK for serverless GPU and CPU workloads without a Docker-first workflow. The current bridge still implements `runpod_pod_v1`; Flash and generic Serverless endpoints should become separate adapters rather than being forced through pod startup scripts.

There are two names to keep distinct:

- Flash: the `runpod-flash` Python SDK and `flash` CLI for Python functions, apps, endpoints, and environments.
- Flashboot: a Serverless endpoint startup option exposed in Flash endpoint parameters and `runpodctl serverless --flash-boot`.

## High-Value Additions

### Adopted Now

- `runpodctl` read-only fallback: `pod-ssh-info`, billing commands with `--backend runpodctl`, and `render-runpodctl-create`.
- Platform-side runtime backstop metadata: `budget.terminate_after_minutes` renders to `runpodctl pod create --terminate-after`.
- Explicit RunPod network-volume S3 egress mode: `runpod_network_volume_s3`.
- Operator guidance for restricted API keys and cost centers without claiming unsupported programmatic lifecycle.

### 1. Flash Adapter: `runpod_flash_v1`

Use when a workload is naturally a Python function, model inference call, image/text generation job, evaluator, sharded mapper, or small batch transform.

Useful RunPod surface:

- `@Endpoint` marks Python functions for remote GPU/CPU execution and returns results to the local control flow.
- Queue endpoints fit async batch jobs and long-running computations.
- Load-balanced endpoints fit HTTP APIs and low-latency request/response services.
- Flash apps deploy multiple independent Serverless endpoints with isolated environments.
- Flash can attach network volumes at `/runpod-volume/` for persistent model/data caches.

Bridge shape:

- Add `provider.adapter: runpod_flash_v1`.
- Add a top-level `flash` block with app name, environment, endpoint type, Python entrypoint, handler/function names, GPU/CPU choice, workers min/max, idle timeout, execution timeout, dependency policy, volume policy, and undeploy policy.
- Treat `flash build` as a local dry-run artifact check.
- Treat `flash deploy` or direct endpoint creation as paid mutation requiring the same launch gates as pods.
- Require endpoint job output or fetched artifacts before success; deployed endpoint state is not enough.
- Require `flash undeploy`, environment delete, or documented retention at closeout.

### 2. Serverless Endpoint Adapter: `runpod_serverless_v1`

Use when the workload already has a Serverless template, Hub listing, custom container, or existing endpoint ID.

Useful RunPod surface:

- `runpodctl serverless` can list, get, create, update, and delete endpoints.
- Endpoint URLs support async `/run`, sync `/runsync`, health checks, and job status.
- Serverless billing has a dedicated `/billing/endpoints` REST history endpoint.
- `flash-boot` can improve worker startup when cached container images apply.

Bridge shape:

- Add read-only endpoint list/get first, then guarded create/delete.
- Add job submit/status/result verification with declared output schemas and artifact hashes.
- Add endpoint billing closeout separate from pod billing.
- Keep template/Hub deployment separate from pod templates because Serverless templates have different constraints.

### 3. Cost Centers And Billing Hygiene

RunPod now documents cost centers for Pods, Serverless endpoints, network volumes, and Instant Clusters. The bridge should preserve cost attribution in manifests and closeouts.

Bridge shape:

- Add optional `billing.cost_center` and `billing.project_code` manifest fields when RunPod exposes API support or `runpodctl` support.
- Keep local fallback reporting from pod runtime x hourly rate.
- Prefer REST billing records for pods, endpoints, and network volumes when reachable.
- Include uncategorized resource checks in operator dashboards once the API/CLI exposes them.

### 4. Official Agent Skills And MCP Servers

RunPod now documents a RunPod agent skills package and two MCP servers:

- API MCP server for Pods, Serverless endpoints, templates, volumes, and registries.
- Docs MCP server for documentation lookup without auth.

Bridge implication:

- Keep this bridge as the policy and contract layer. Do not let generic natural-language RunPod tools bypass launch gates.
- Add setup docs for optional MCP/docs lookup, but keep paid mutation centralized in `runpod-bridge`.
- In worker sandboxes, continue proving API reachability before mutation; MCP availability does not prove shell REST reachability.

### 5. `runpodctl` Fallback And File Transfer

RunPod CLI is now documented as `runpodctl`, with resource management, SSH setup, billing, and secure peer-to-peer `send`/`receive` file transfer.

Bridge shape:

- Detect `runpodctl` in `doctor`.
- Prefer bridge REST for audited pod lifecycle until a runpodctl mutation backend is implemented.
- Use `pod-ssh-info` to fetch SSH command details through `runpodctl ssh info`.
- Use `billing-pods --backend runpodctl`, `billing-endpoints --backend runpodctl`, and `billing-network-volumes --backend runpodctl` for read-only billing checks when `runpodctl` is configured.
- Use `render-runpodctl-create` to inspect the `runpodctl pod create` command and confirm `--terminate-after` is present.
- Use `runpodctl send`/`receive` only as an explicit operator-assisted recovery or artifact transfer path because connection codes are operational secrets and should not be posted in Linear.

### 6. Network Volume S3 Egress

RunPod network volumes expose an S3-compatible API through datacenter-specific endpoints. This is the strongest closeout path for retained artifacts because the pod can be deleted before artifacts are pulled.

Bridge shape:

- Use `artifact_egress.mode: runpod_network_volume_s3`.
- Require `runpod.networkVolumeId`, Secure Cloud, a data center ID or endpoint reference, and a runtime credential reference.
- Pull with AWS-compatible tooling using `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; these are RunPod S3 API keys, not `RUNPOD_API_KEY`.
- Use object head/hash checks for declared artifacts instead of recursive listing as the primary proof.

### 7. Instant Clusters Watchlist: `runpod_cluster_v1`

Instant Clusters are managed multi-node compute clusters for distributed training or inference. Official docs describe high-speed networking, 2-8 node defaults, Slurm support, and larger sales-assisted clusters.

Bridge shape:

- Treat as a later adapter, not a pod variant.
- Require scheduler-aware stage contracts: rank/world-size setup, node health, rendezvous, checkpoint cadence, multi-node artifact aggregation, cost cap, and teardown proof.
- Useful for huge workloads only after the pod and serverless lanes are mature.

## Near-Term Implementation Order

1. Keep `runpod_pod_v1` as the only mutating adapter until Flash/Serverless gates are implemented.
2. Add read-only capability metadata and docs for Flash, Serverless endpoints, `runpodctl`, MCP, cost centers, and Instant Clusters.
3. Expose endpoint and network-volume billing commands so closeout can track non-pod costs.
4. Add a runpodctl mutation backend only if JSON output and post-create pod ID capture are reliable enough for audit records.
5. Prototype `runpod_flash_v1` locally with `flash build`, manifest validation, and undeploy planning before any paid deploy.
6. Add a cheap Flash smoke only after the local machine has `flash`, auth, and a public-safe Python function contract.

## Official Sources

- RunPod Flash GA blog: https://www.runpod.io/blog/flash-is-ga
- Flash overview: https://docs.runpod.io/flash/overview
- Flash endpoint types and parameters: https://docs.runpod.io/flash/create-endpoints
- Flash deploy apps: https://docs.runpod.io/flash/apps/deploy-apps
- Flash CLI: https://docs.runpod.io/flash/cli/overview
- RunPod agent skills: https://docs.runpod.io/get-started/agent-skills
- RunPod MCP servers: https://docs.runpod.io/get-started/mcp-servers
- RunPod CLI: https://docs.runpod.io/runpodctl/overview
- RunPod CLI pods: https://docs.runpod.io/runpodctl/reference/runpodctl-pod
- RunPod CLI billing: https://docs.runpod.io/runpodctl/reference/runpodctl-billing
- RunPod CLI SSH: https://docs.runpod.io/runpodctl/reference/runpodctl-ssh
- Serverless CLI: https://docs.runpod.io/runpodctl/reference/runpodctl-serverless
- RunPod network-volume S3 API: https://docs.runpod.io/storage/s3-api
- RunPod API keys: https://docs.runpod.io/get-started/api-keys
- Billing overview: https://docs.runpod.io/accounts-billing/billing
- Cost centers: https://docs.runpod.io/accounts-billing/cost-centers
- Instant Clusters: https://docs.runpod.io/instant-clusters
