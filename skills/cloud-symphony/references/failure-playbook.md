# RunPod Failure Playbook

Use this when a pod launch, monitor, or artifact fetch does not match the expected contract. Keep diagnosis provider-neutral where possible, but record RunPod-specific evidence in the run packet.

## First Decision

1. Check spend state first: list active pods by the expected name prefix and identify the cleanup owner.
2. Preserve evidence: pod ID, machine ID, data center, image, create request size, runtime metrics samples, proxy/TCP responses, and any fetched logs.
3. Do not keep spending to learn basic facts. If there is no live productivity channel and no immediate operator peek path, clean up and relaunch only after the manifest is improved.

## Common Failure Modes

| Symptom | Likely Meaning | Action |
| --- | --- | --- |
| `POST /pods` returns HTTP 500 | RunPod create path or account/provider capacity issue, not workload execution | Stop retrying the same manifest. Try one tiny no-volume CPU or GPU provider smoke later, then escalate with request timestamp and payload class. |
| GPU `POST /pods` returns `no instances` for explicit `gpuTypeIds` and `dataCenterIds` | Could be either catalog mismatch or zero current capacity; REST uses the same wording for both | Run `cloud-bridge gpu-catalog --manifest <manifest> --json`. If GraphQL shows no offered GPU/DC combo, change `gpuTypeIds` or data centers instead of retrying. If offered but unavailable, treat it as capacity. |
| Pod is billed or `RUNNING`, but `publicIp`, `portMappings`, and runtime are empty | Pre-runtime platform init, image pull, mount, or scheduling hang | Treat as no container truth. If network volume pins the pod to one host, try no-volume smoke in another pool before blaming the workload. |
| GraphQL runtime appears once with tiny uptime, then resets or stays tiny | Crash loop or repeated container restart | Clean up unless SSH/log proof is immediately available. Relaunch only after entrypoint logs persist outside the crashing process. |
| GraphQL `uptimeInSeconds` is negative | Invalid provider telemetry or unhealthy pod agent | Do not infer productivity. Require `/healthz`, SSH/log tail, or artifact/status packet proof; otherwise clean up. |
| Workload reaches final phase, then phase resets to `started` | Container entrypoint may be exiting while the Pod desired state remains running, or a hidden failure is restarting the workload | Add `startup.terminal_hold.mode: sleep_infinity`, write final status before idling, and require orchestrator cleanup after artifact fetch. |
| REST `publicIp` or `portMappings` empty, but HTTP proxy URL works | REST field lag | Probe `https://<pod-id>-<internal-port>.proxy.runpod.net/` for declared HTTP ports before declaring proxy unavailable. |
| HTTP proxy returns 404 | Wrong path, undeclared port, completed workload with stopped progress server, or workload service not ready | Interpret only against declared ports and expected paths. If the manifest has separate progress and artifact ports, probe the progress port for `status.json`, logs, or `/healthz` before cleanup. Repeated 404 on declared `/healthz` after runtime starts means progress server did not bind, not that the workload failed. |
| Artifact TCP/HTTP port refuses connection while workload runs | Completion-only artifact server has not started | This is expected for `startup.inspection.http_artifact_server_port`. It does not prove productivity. |
| Git clone fails before workload command | Image lacks `git` during bridge bootstrap | Do not install `git` in `startup.commands`; that runs too late. Use an image canary, declare `runpod.image_capabilities: ["git"]`, or use inline, prepared_snapshot, or object-store bootstrap. |
| Private image launch fails before entrypoint | RunPod registry auth or image pullability was not proven provider-side | Configure RunPod registry auth from a trusted orchestrator or record an exact image-pull canary as `runpod.image_pull_verified: true`. Local Docker, GitHub, or ECR auth on the worker is not enough. |
| `dockerStartCmd` launch fails near large inline payload size | Create request too large | Run `preflight` and check `payload_post_body_bytes`. Compress inline material with gzip/base64 or move it into a repo, prepared snapshot, or object-store handoff. |
| GPU workload never reaches app code | Scheduling/image/provider issue may be hiding as workload failure | Run a tiny image-native GPU smoke first: `nvidia-smi` plus a minimal HTTP `/healthz` server. Escalate GPU family only after that smoke isolates the pool. |

## Runtime Metrics Rule

Use `runtime-metrics` early and again after a few minutes:

```bash
cloud-bridge runtime-metrics <pod-id> --expected-elapsed-minutes 5 --json --out .runtime/<pod-id>-runtime-1.json
cloud-bridge runtime-metrics <pod-id> --previous .runtime/<pod-id>-runtime-1.json --json --out .runtime/<pod-id>-runtime-2.json
```

Runtime metrics are negative proof. Tiny, resetting, missing, or negative uptime can prove the pod is unhealthy or unknowable. Utilization samples do not prove useful progress.

Then classify live progress with repeated samples:

```bash
cloud-bridge progress-report <manifest> <pod-id> --json --out .runtime/<pod-id>-progress-1.json
cloud-bridge progress-report <manifest> <pod-id> --previous .runtime/<pod-id>-progress-1.json --json --out .runtime/<pod-id>-progress-2.json
```

If the classification is `harness_alive_progress_unproven`, the monitor is alive but workload progress is still unproven. Do not call it healthy progress, and do not call it a provider outage from one failed probe.

For any other state, still report `classification.state` verbatim. A provider outage may be reported only when independent provider probes support it; one failed progress probe is classification evidence, not an outage label.

## Relaunch Ladder

Before relaunching the real workload, choose the smallest test that removes one variable:

1. No-volume CPU smoke with inline commands and no git.
2. Exact image bootstrap smoke that proves required binaries such as `git`, `python`, `bash`, `nvidia-smi`, or domain tools exist before bridge bootstrap.
3. Same data center and volume smoke if the workload depends on a network volume.
4. Tiny GPU smoke if GPU scheduling or image startup is suspected.
5. Full workload only after the smoke route proves create, boot, progress signal, artifact egress, hashing, and cleanup.

For GPU launch retries, insert this before the ladder when the manifest names GPU IDs:

```bash
cloud-bridge gpu-catalog --manifest <manifest> --json --out .runtime/gpu-catalog.json
```

GraphQL `gpuTypes` proves only the general GPU/DC catalog and current availability. It does not prove the smaller subset of machines that can attach a declared network volume has capacity. If no-volume probes work but with-volume launch fails, diagnose the network-volume capacity intersection separately.

## Closeout Language

Use precise outcomes, but keep machine fields separate. The canonical mapping lives in `docs/runpod-status-taxonomy.md`.

| Human label | Required machine evidence |
| --- | --- |
| `blocked_provider_create` | `remote_run_record.status` is create-blocked or create-failed and no pod ID exists. |
| `blocked_provider_runtime` | Pod exists, but runtime/progress never proves container truth. |
| `crash_loop_suspected` | `progress_report.classification.state=pod_unhealthy_or_unobservable` or runtime uptime resets. |
| `blocked_bootstrap` | Fetched status/logs show bootstrap failure before workload commands. |
| `blocked_observability` | Pod may be running, but no progress/artifact channel proves workload state. |
| `succeeded_artifacts_verified_cleanup_done` | `remote_run_record.status=succeeded`, artifact hashes present, and cleanup verified. |
