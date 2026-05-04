# RunPod Observability Ladder

Use this ladder before deciding whether a running pod is productive.

## Rungs

1. Provider allocation
   - Signals: `desiredStatus`, machine ID, GPU/CPU fields, public IP, port mappings, cost rate.
   - Meaning: RunPod allocated or is trying to allocate resources.
   - Does not prove: container startup, image pull completion, mounted network volume health, or workload progress.
   - Caveat: REST `publicIp` and `portMappings` can lag a working HTTP proxy URL. For `/http` ports, probe `https://<pod-id>-<internal-port>.proxy.runpod.net/...` directly before concluding the proxy path is unavailable.

2. Provider runtime metrics
   - Signals: RunPod GraphQL `pod.runtime.uptimeInSeconds`, `container.cpuPercent`, `container.memoryPercent`, and GPU utilization fields.
   - Meaning: the provider can see a running container runtime sample.
   - Strong negative proof: if `uptimeInSeconds` resets between samples, or is still tiny after a long elapsed allocation time, assume a container crash-restart loop until logs prove otherwise.
   - Invalid telemetry: if `uptimeInSeconds` is negative, treat the provider runtime sample as invalid or the pod agent as unhealthy. Do not infer productivity from it.
   - Does not prove: useful workload progress, artifact creation, or domain success. A non-zero CPU sample can be only the restart moment; a zero sample can be only an idle poll.

3. Startup reached workload harness
   - Signals: `status.json` exists with `status: running`, `monitor_events.ndjson` has fresh heartbeat, `startup.log` exists.
   - Meaning: the bridge startup script is alive inside the container.
   - Does not prove: domain task is making useful progress unless phase/log/progress values advance.

4. Live peek channel
   - Signals: `startup.progress.http_status_server_port` `/healthz`, SSH tail of `startup.log`, or fetched status/heartbeat packet.
   - Meaning: agents can check workload phase, heartbeat freshness, and log growth while work is still running.
   - Use `/healthz` only for sanitized smoke metadata unless `auth_token_ref` is configured. Use SSH/log tail for private workloads. `runpodctl ssh info` can fetch SSH connection details when installed, but it is not a documented generic exec channel.

5. Completion inspection
   - Signals: `startup.inspection.http_artifact_server_port` responds, `status.json` is final, expected artifact paths are fetchable.
   - Meaning: the workload reached `inspection_hold` after startup and validation completed.
   - A refused connection before this phase is expected and only means the completion server is not up yet.

6. Artifact proof and cleanup
   - Signals: declared artifacts fetched, hashes computed, validation commands passed, forbidden-marker scan passed, cleanup verified.
   - Meaning: the bridge can claim execution success at the declared claim level.

## Operator Rule

Never claim productivity from provider `RUNNING`, billing, a refused artifact port, or a single utilization sample. Claim productivity only from a fresh workload heartbeat, live `/healthz`, SSH/log tail, or fetched status/log packet. Use runtime metrics as a fast negative check:

```bash
bin/runpod-bridge runtime-metrics <pod-id> --expected-elapsed-minutes 5 --json --out .runtime/<pod-id>-runtime.json
bin/runpod-bridge runtime-metrics <pod-id> --previous .runtime/<pod-id>-runtime.json --json
```

If the second sample has lower uptime, a 30 minute old allocation reports container uptime near zero, or uptime is negative, stop spending unless an operator can immediately inspect logs or a workload heartbeat/artifact packet.

Before paid launch, run:

```bash
bin/runpod-bridge productivity-plan <manifest>
bin/runpod-bridge source-check <manifest> --execute
bin/runpod-bridge preflight <manifest>
```

`preflight` reports rendered create-payload size. Keep inline startup payloads small; compress large embedded material with gzip/base64 or move it to a repo/snapshot/object-store handoff. A live smoke found an empirical failure around a 65KB `dockerStartCmd` request even though the public RunPod docs do not document that boundary.

For CPU pods, keep `containerDiskInGb` at or below 20 unless the selected CPU flavor is known to accept more. Small CPU stock such as `cpu3c` can reject larger container disks after a paid create attempt.
