# RunPod Failure Playbook

Use this when a pod launch, monitor, or artifact fetch does not match the expected contract. Keep diagnosis provider-neutral where possible. Record RunPod-specific evidence in the run packet.

## First Decision

1. Check spend state first: list active pods by the expected name prefix and identify the cleanup owner.
2. Preserve evidence: pod ID, machine ID, data center, image, create request size, runtime metrics samples, proxy/TCP responses, and any fetched logs.
3. Stop spending to learn basic facts. If there is no live productivity channel and no immediate operator peek path, clean up and relaunch only after the manifest is improved.

## Common Failure Modes

| Symptom | Likely Meaning | Action |
| --- | --- | --- |
| `POST /pods` returns HTTP 500 | RunPod create path or account/provider capacity issue | Stop retrying the same manifest. Try one tiny no-volume CPU or GPU provider smoke later, then escalate with request timestamp and payload class. |
| Pod is billed or `RUNNING`, but `publicIp`, `portMappings`, and runtime are empty | Pre-runtime platform init, image pull, mount, or scheduling hang | Treat as no container truth. If a network volume pins the pod to one host, try a no-volume smoke in another pool before blaming the workload. |
| GraphQL runtime appears once with tiny uptime, then resets or stays tiny | Crash loop or repeated container restart | Clean up unless SSH/log proof is immediately available. Relaunch only after entrypoint logs persist outside the crashing process. |
| GraphQL `uptimeInSeconds` is negative | Invalid provider telemetry or unhealthy pod agent | Treat as unknowable. Require `/healthz`, SSH/log tail, or an artifact/status packet before continuing spend. Otherwise clean up. |
| REST `publicIp` or `portMappings` empty, but HTTP proxy URL works | REST field lag | Probe `https://<pod-id>-<internal-port>.proxy.runpod.net/` for declared HTTP ports before declaring proxy unavailable. |
| HTTP proxy returns 404 | Wrong path, undeclared port, or workload service not ready | Interpret only against declared ports and expected paths. Repeated 404s on declared `/healthz` after runtime starts mean the progress server did not bind. |
| Artifact TCP/HTTP port refuses connection while workload runs | Completion-only artifact server has not started | Expected for `startup.inspection.http_artifact_server_port`. Refusal alone says nothing about productivity. |
| Git clone fails before workload command | Image lacks `git` during bridge bootstrap | Installing `git` in `startup.commands` runs too late. Use an image canary, declare `runpod.image_capabilities: ["git"]`, or use inline/snapshot/object-store bootstrap. |
| `dockerStartCmd` launch fails near large inline payload size | Create request too large | Run `preflight` and check `payload_post_body_bytes`. Compress inline material with gzip and base64, or move it into a repo, snapshot, or object-store handoff. |
| GPU workload never reaches app code | Scheduling, image, or provider issue may be hiding as workload failure | Run a tiny image-native GPU smoke first: `nvidia-smi` plus a minimal HTTP `/healthz` server. Escalate GPU family only after that smoke isolates the pool. |

## Runtime Metrics Rule

Use `runtime-metrics` early and again after a few minutes:

```bash
runpod-bridge runtime-metrics <pod-id> --expected-elapsed-minutes 5 --json --out .runtime/<pod-id>-runtime-1.json
runpod-bridge runtime-metrics <pod-id> --previous .runtime/<pod-id>-runtime-1.json --json --out .runtime/<pod-id>-runtime-2.json
```

Runtime metrics are negative proof. Tiny, resetting, missing, or negative uptime can show the pod is unhealthy or unknowable. Utilization samples by themselves do not prove useful progress.

## Relaunch Ladder

Before relaunching the real workload, choose the smallest test that removes one variable:

1. No-volume CPU smoke with inline commands and no git.
2. Exact image bootstrap smoke that proves required binaries such as `git`, `python`, `bash`, `nvidia-smi`, or domain tools exist before bridge bootstrap.
3. Same data center and volume smoke if the workload depends on a network volume.
4. Tiny GPU smoke if GPU scheduling or image startup is suspected.
5. Full workload only after the smoke route proves create, boot, progress signal, artifact egress, hashing, and cleanup.

## Closeout Language

Use precise outcomes:

- `blocked_provider_create`: create API failed before a pod existed.
- `blocked_provider_runtime`: pod allocated but no container/runtime truth was reachable.
- `crash_loop_suspected`: runtime uptime reset or stayed tiny after meaningful elapsed time.
- `blocked_bootstrap`: bridge bootstrap failed before workload commands ran.
- `blocked_observability`: pod may be running, but no live productivity channel exists.
- `succeeded_artifacts_verified_cleanup_done`: declared artifacts fetched, validated, hashed, and cleanup verified.
