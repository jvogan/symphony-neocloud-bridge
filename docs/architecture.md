# Architecture

## Separation Of Concerns

```text
Domain repo
  owns workload commands, input data policy, artifact contract, and domain self-checks

Symphony Cloud Bridge
  owns remote compute lifecycle, startup script rendering, artifact capture, cost reporting, recovery, closeout, and outcome formatting

Linear
  owns work authorization, dependencies, audit trail, and closeout comments

RunPod
  owns remote execution resources
```

## Minimal Flow

1. Read Linear issue or local launch manifest.
2. Validate launch policy:
   - `remote_launch_allowed: true`
   - explicit `launch_authorization`
   - budget/time limit
   - cleanup policy
   - expected artifacts
   - validation commands
   - passing contract self-check
   - no literal secrets
3. Run contract self-check and local preflight.
4. Prepare a launch packet with `launch_manifest.json`, `local_preflight.json`, `startup.sh`, and `provider_handoff.json`.
5. If the worker shell has no RunPod API reachability, stop worker-side mutation and pass `provider_handoff.json` to the orchestrator.
6. Acquire the local launch lock, then create or start RunPod pod through `run-handoff`, `run-remote`, the guarded REST adapter, or RunPod MCP tools.
7. Poll pod state and cost fields while the workload self-reports heartbeat/status files.
8. Run workload through startup command, template, SSH/SCP, or future log/exec integration.
9. Collect logs, status files, artifacts, egress status, cost records, and hashes.
10. Run declared artifact checks.
11. Stop/delete pod unless retention is approved. If a network volume is attached, delete the pod and preserve the volume instead of relying on stop semantics.
12. Emit `symphony-outcome` with `remote_execution_by`.

## Current Tool Surface Observed On 2026-04-30

RunPod MCP tools discoverable in Codex:

- `create_pod`
- `list_pods`
- `get_pod`
- `update_pod`
- `start_pod`
- `stop_pod`
- `delete_pod`
- `create_template`
- `list_templates`
- `get_template`
- `update_template`
- `delete_template`
- `create_network_volume`
- `list_network_volumes`
- `get_network_volume`
- `update_network_volume`
- `delete_network_volume`
- `list_endpoints`
- `get_endpoint`
- `create_endpoint`
- `update_endpoint`
- `delete_endpoint`
- `list_container_registry_auths`
- `get_container_registry_auth`
- `create_container_registry_auth`
- `delete_container_registry_auth`

RunPod REST API support now exists in the local CLI for:

- creating pods through `POST /pods`
- listing pods through `GET /pods`
- fetching pods through `GET /pods/{podId}`
- stopping pods through `POST /pods/{podId}/stop`
- deleting pods through `DELETE /pods/{podId}`
- fetching pod billing through `GET /billing/pods`
- fetching Serverless and network-volume billing through dedicated billing endpoints
- listing/fetching network volumes and templates
- optional `runpodctl` shellouts for SSH info, billing reads, and non-mutating pod create command rendering

The local CLI uses the REST API only when `RUNPOD_API_KEY` is present and the caller passes the explicit execute confirmation flag.

Not yet represented as direct MCP tools:

- pod container log retrieval
- in-pod command execution

SSH connection command discovery is available when `runpodctl` is installed through `cloud-bridge pod-ssh-info <pod-id>`.

V1 should treat startup-command execution plus workload-written logs/status/artifacts as the reliable automation path. SSH/SCP can be enabled for runs that need interactive recovery or direct artifact transfer.

See [runpod-worker-readiness.md](runpod-worker-readiness.md) for the operational enablement checklist.

## Success Gate

Do not report success from:

- pod created
- pod started
- pod stopped
- command submitted
- logs exist

Report success only when declared artifact checks pass and cleanup is verified as stopped, deleted, or already absent.
