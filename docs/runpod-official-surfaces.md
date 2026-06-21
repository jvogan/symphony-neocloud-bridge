# RunPod Official Surfaces

Checked against official RunPod docs and blog posts on 2026-05-03; rechecked route, registry, and network-volume S3 updates on 2026-05-15.

## REST API

- API overview: https://docs.runpod.io/api-reference/overview
- Pod create/list/get/update/start/stop/delete/reset/restart: https://docs.runpod.io/api-reference/pods/POST/pods
- Pod billing history: https://docs.runpod.io/api-reference/billing/GET/billing/pods
- Serverless billing history: https://docs.runpod.io/api-reference/billing/GET/billing/endpoints
- Network volume billing history: https://docs.runpod.io/api-reference/billing/GET/billing/networkvolumes
- Network volumes: https://docs.runpod.io/storage/network-volumes
- Templates: https://docs.runpod.io/api-reference/templates/POST/templates

## GraphQL API

- GraphQL overview and endpoint: https://docs.runpod.io/sdks/graphql/configurations
- Pod query examples with `runtime.uptimeInSeconds`, container CPU/memory samples, GPU utilization samples, and ports: https://docs.runpod.io/sdks/graphql/manage-pods

## Flash And Serverless

- RunPod Flash GA announcement: https://www.runpod.io/blog/flash-is-ga
- Flash overview: https://docs.runpod.io/flash/overview
- Flash endpoint types: https://docs.runpod.io/flash/create-endpoints
- Flash app deployment: https://docs.runpod.io/flash/apps/deploy-apps
- Flash CLI: https://docs.runpod.io/flash/cli/overview
- Flash endpoint parameters, including `flashboot`: https://docs.runpod.io/flash/configuration/parameters
- Flash storage and network volumes: https://docs.runpod.io/flash/configuration/storage
- RunPod CLI Serverless endpoint management: https://docs.runpod.io/runpodctl/reference/runpodctl-serverless

## Agent And Operator Tooling

- RunPod agent skills: https://docs.runpod.io/get-started/agent-skills
- RunPod MCP servers: https://docs.runpod.io/get-started/mcp-servers
- RunPod CLI overview: https://docs.runpod.io/runpodctl/overview
- RunPod CLI pods, including `--stop-after` and `--terminate-after`: https://docs.runpod.io/runpodctl/reference/runpodctl-pod
- RunPod CLI pod create flags, including `--registry-auth-id`: https://docs.runpod.io/runpodctl/reference/runpodctl-create-pod
- RunPod CLI billing for pods, serverless, and network volumes: https://docs.runpod.io/runpodctl/reference/runpodctl-billing
- RunPod CLI SSH info: https://docs.runpod.io/runpodctl/reference/runpodctl-ssh
- RunPod CLI file send: https://docs.runpod.io/runpodctl/reference/runpodctl-send
- RunPod CLI file receive: https://docs.runpod.io/runpodctl/reference/runpodctl-receive
- RunPod CLI registry auth: https://docs.runpod.io/runpodctl/reference/runpodctl-registry

## Larger Compute And Cost Surfaces

- Billing overview: https://docs.runpod.io/accounts-billing/billing
- Cost centers: https://docs.runpod.io/accounts-billing/cost-centers
- API keys: https://docs.runpod.io/get-started/api-keys
- Instant Clusters: https://docs.runpod.io/instant-clusters

## Operational Notes

- Pods expose HTTP and TCP ports with manifest strings such as `8888/http` and `22/tcp`.
- RunPod documents the HTTP proxy URL shape as `https://<pod-id>-<internal-port>.proxy.runpod.net`; this path should be probed directly for HTTP smokes.
- HTTP proxy inspection is public and should be limited to short-lived sanitized smoke artifacts.
- Direct TCP uses pod `publicIp` and `portMappings`; mappings can change after reset.
- The REST docs document `publicIp` and `portMappings`, but the bridge observed those fields lagging a working HTTP proxy path. Treat REST networking fields as useful metadata, not the only readiness signal for `/http` services.
- The REST docs do not document a maximum `dockerStartCmd` or POST body size. The bridge uses an empirical guard because a live smoke failed near a 65KB rendered startup command.
- Full SCP requires public IP support, `22/tcp`, an SSH daemon in the image/template, and SSH key auth.
- Network volumes for Pods require Secure Cloud, attach at deployment time, and retain data after pod deletion.
- Network volumes constrain scheduling to compatible data-center capacity; manifests should record the data center and closeout owner when a volume is retained.
- RunPod network-volume S3 is suitable for durable file movement without keeping a pod alive.
- Billing closeout should prefer `GET /billing/pods` when available, then fall back to runtime times pod cost fields.
- Runtime metrics closeout and monitoring should use GraphQL `pod.runtime` as read-only health evidence. Tiny or resetting `uptimeInSeconds` can prove a likely crash loop; utilization samples alone do not prove productivity or artifact success.
- Negative GraphQL `runtime.uptimeInSeconds` is not documented as a normal state. The bridge treats it as invalid provider telemetry or pod-agent trouble and fails closed unless workload-level evidence is available.
- Serverless endpoint closeout should prefer `GET /billing/endpoints`; network volume retention should prefer `GET /billing/networkvolumes`.
- When `runpodctl` is installed and configured, it can fetch SSH commands and billing history without the bridge carrying another REST adapter path for those reads.
- `budget.terminate_after_minutes` maps to `runpodctl pod create --terminate-after`; the REST create path currently records the backstop but does not enforce it platform-side.
- RunPod network-volume S3 uses datacenter-specific endpoints such as `https://s3api-us-ks-2.runpod.io/` and separate S3 API credentials, not `RUNPOD_API_KEY`.
- RunPod network-volume S3 maps Pod `/workspace/path` to `s3://NETWORK_VOLUME_ID/path`; use exact `head-object`/`cp` checks for declared archives instead of recursive listing as primary proof on large directories.
- The current `runpodctl pod` and official runpodctl reference cover create/list/get/start/stop/restart/reset/update/delete; the bridge should not promise generic pod log streaming or in-pod exec through runpodctl. Use workload `/healthz`, artifact packets, or SSH/SCP where the manifest declares them.
- AWS S3 presigned upload is a strong companion path for direct pod-to-S3 artifact egress without AWS credentials inside the pod; use `artifact_egress.mode: aws_s3_presigned_upload` and inject URLs only at runtime.
- API keys support restricted/read-only/all permissions in the console; the bridge did not verify an official API for programmatic per-run key creation or expiry.
- Cost centers apply to Pods, Serverless endpoints, network volumes, and Instant Clusters. Bridge manifests should record intended `billing.cost_center` and `billing.project_code`, then operator closeout should verify provider-side assignment through the supported surface.
- RunPod Flash is a separate Serverless/Python function lane, not the same adapter as the current pod lifecycle runner.
- Flash docs currently list Python 3.10, 3.11, 3.12, and 3.13 support; adapter contracts should pin the Python minor version used for local validation and deploy.
- Flashboot is a Serverless worker startup optimization; enabling it does not replace artifact proof, budget gates, or undeploy/cleanup proof.
- `runpod.interruptible: true` should require checkpoint/resume policy and durable artifact egress before paid launch.
- Instant Clusters are a separate multi-node adapter candidate, not a pod profile.
