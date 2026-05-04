# Remote Smoke Runbook

Use this only after local validation passes and a small spend is approved.

## Preconditions

- `RUNPOD_API_KEY` is available in the shell.
- The shell running remote commands has outbound DNS/TCP access. Symphony Codex worker sandboxes may not; in that case, run the remote create/verify/cleanup steps from the orchestrator or trusted `after_run` hook after the worker has produced a validated packet.
- The manifest has `remote_launch_allowed: true`.
- The manifest has explicit `launch_authorization`.
- `budget.max_estimated_cost_usd` is less than or equal to the approved spend ceiling.
- `budget.terminate_after_minutes` is set when using the `runpodctl` create path as a platform-side cleanup backstop.
- For normal workloads, `repo.url_or_path` points to a reachable repository and `repo.commit_or_snapshot` is an immutable commit SHA or snapshot/archive digest.
- For git-source workloads, the exact pod image or template has been proven to include `git` before bridge bootstrap. Declare that as `runpod.image_capabilities: ["git"]` only after an image canary; installing git in `startup.commands` is too late because repo bootstrap runs first.
- For the included public smoke, `repo.source` may be `inline_commands` and must stay CPU-only with a small budget.
- Cleanup owner is known.
- If more than one orchestrator can run on the same host or mounted workspace, set a shared `RUNPOD_BRIDGE_LOCK_DIR` so `run-remote` and `run-handoff` block duplicate paid launches before creation.
- `preflight` and `egress-plan` have been reviewed for large/huge workloads.
- `preflight` shows `payload_post_body_bytes`. If the rendered RunPod POST body is near the warning threshold, compress large inline scripts/data or move them to a repo/snapshot/object store before spending. Do not rely on RunPod returning a useful error for oversized startup payloads.

## Preferred One-Command Run

Use `run-remote` for normal smokes. It acquires the local launch lock, performs the guarded create request, downloads and hashes the artifact packet, and attempts cleanup in a `finally` path when a pod ID exists.

```bash
bin/runpod-bridge run-remote path/to/launch_manifest.json \
  --out-dir .runtime/remote-smoke \
  --max-spend-usd 5 \
  --verification-mode auto \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

Review `.runtime/remote-smoke/remote_run_record.json`. A successful run has `status: succeeded`, a created pod ID, a verification result with `ok: true`, and a cleanup record with `status: submitted` or `already_absent`.

`--verification-mode auto` tries direct TCP packet verification first, then falls back to the RunPod HTTP proxy. Use `--verification-mode tcp` for smokes that explicitly expose a TCP artifact port and should fail fast if no mapping appears. For `/http` ports, the proxy URL is derived from pod ID and internal port, so packet verification can succeed even while REST `publicIp` or `portMappings` fields are still empty.

## Worker Handoff Mode

When a worker can validate locally but cannot reach RunPod REST, it should stop before mutation and emit a handoff packet:

```bash
bin/runpod-bridge prepare path/to/launch_manifest.json --out-dir runpod-execution
bin/runpod-bridge validate-handoff runpod-execution/provider_handoff.json
```

The orchestrator then performs the paid step:

```bash
bin/runpod-bridge run-handoff runpod-execution/provider_handoff.json \
  --out-dir .runtime/handoff-run \
  --max-spend-usd 5 \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

`run-handoff` writes `.runtime/handoff-run/handoff_run_record.json` and a nested remote run record. The final closeout should report `remote_execution_by: orchestrator`.

To process a local handoff queue instead of one file:

```bash
bin/runpod-bridge orchestrator-scan .runtime
bin/runpod-bridge orchestrator-once .runtime --out-root .runtime/orchestrator --max-spend-usd 5
```

## Manual Dry-Run Request

```bash
bin/runpod-bridge create-pod path/to/launch_manifest.json --out-dir .runtime/remote-smoke --max-spend-usd 5
```

Review `.runtime/remote-smoke/runpod_resource_record.json`. It should contain a redacted request and no response.

## Manual Create

```bash
bin/runpod-bridge create-pod path/to/launch_manifest.json \
  --out-dir .runtime/remote-smoke \
  --max-spend-usd 5 \
  --execute \
  --yes-create-paid-runpod
```

Record the returned pod ID.

If `create-pod` fails during duplicate checking or creation, inspect `.runtime/remote-smoke/runpod_resource_record.json`. The bridge records `failed_duplicate_check` before creation and `failed_create_request` when the POST outcome is unknown.

## Monitor

```bash
bin/runpod-bridge get-pod POD_ID
bin/runpod-bridge runtime-metrics POD_ID --expected-elapsed-minutes 5 --json --out .runtime/POD_ID-runtime-1.json
bin/runpod-bridge runtime-metrics POD_ID --previous .runtime/POD_ID-runtime-1.json --json
```

The RunPod REST API does not currently provide direct pod log streaming through this bridge. GraphQL runtime metrics can flag a crash loop when `uptimeInSeconds` resets or stays near zero after long elapsed time, but they do not prove useful work. The workload must write logs, heartbeats, status, artifacts, and archive packets into `runpod-execution/`.

If `runtime-metrics` reports `invalid_runtime_telemetry`, for example negative uptime, treat the pod as untrustworthy unless a workload-level heartbeat, SSH/log tail, or artifact packet is reachable. Clean up before running broader GPU retries. For GPU launch diagnosis, prefer one tiny image-native smoke first, such as `nvidia-smi` plus `python3 -m http.server`, then retry a different GPU family only if that smoke passes.

For the sanitized proxy-matrix smoke, the startup contract can hold the pod open briefly and serve `runpod-execution/` over an exposed HTTP port:

```bash
bin/runpod-bridge verify-proxy-packet examples/proxy-matrix/launch_manifest.json \
  POD_ID \
  --port 8000 \
  --out-dir .runtime/proxy-matrix-proxy
```

Treat HTTP proxy verification as a convenience check, not as the durable production artifact channel. RunPod's documented HTTP proxy path is public and Cloudflare-mediated, and this environment observed a proxy fetch failure with HTTP 403 / error code 1010 during a live smoke. Use workspace archive plus SCP, network volume, AWS S3 presigned upload, or object-store upload when artifact retrieval must be reliable or private.

Only interpret HTTP 404s against declared `/http` ports and expected paths. A 404 on an undeclared port or wrong path is not useful evidence; repeated 404s on the declared status or artifact paths after runtime appears are evidence that the workload service did not reach inspection/progress serving.

If the manifest exposes the same artifact server through TCP, use direct TCP verification as the HTTP proxy fallback:

```bash
bin/runpod-bridge verify-tcp-packet examples/proxy-matrix/launch_manifest.json \
  POD_ID \
  --port 8000 \
  --out-dir .runtime/proxy-matrix-tcp
```

TCP exposure is also public and still needs sanitized artifacts or application-layer authentication.

## Cleanup

```bash
bin/runpod-bridge cleanup-pod POD_ID --action delete --out-dir .runtime/remote-smoke --execute --yes-cleanup-runpod
```

Do not consider the smoke complete until cleanup is recorded and a follow-up `get-pod` either reports a terminal state or returns not found after deletion.

## Cost And Recovery

```bash
bin/runpod-bridge cost-report .runtime/remote-smoke/remote_run_record.json --fetch-billing
bin/runpod-bridge recover-run .runtime/remote-smoke/remote_run_record.json
bin/runpod-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
```

Use the billing API when it is reachable. If billing records are delayed or unavailable, close out with the bridge estimate and the cost source set to `runtime_x_cost_fields`.

When `runpodctl` is installed, these read-only fallbacks are available:

```bash
bin/runpod-bridge pod-ssh-info POD_ID
bin/runpod-bridge billing-pods --backend runpodctl --pod-id POD_ID --start-time 2026-05-01T00:00:00Z
bin/runpod-bridge render-runpodctl-create path/to/launch_manifest.json
```
