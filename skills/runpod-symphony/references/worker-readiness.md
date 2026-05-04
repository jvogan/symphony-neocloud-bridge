# RunPod Worker Readiness

Use this before assigning RunPod work to Symphony Codex, Claude Code, or mixed-agent workers.

## Required Discovery

- `runpod-bridge doctor` must find the bridge CLI and the `runpod-symphony` skill.
- `runpod-bridge list-pods --name-prefix definitely-no-match --json` must succeed before a worker performs paid mutation from inside its shell.
- If the worker shell cannot reach RunPod REST or GraphQL, the worker stops at `prepare` plus `validate-handoff`. An unsandboxed orchestrator or trusted hook runs `run-handoff`.
- Linear access covers issue intake, lock and status updates, and `symphony-outcome` closeout. Paid cloud mutation still requires manifest authorization.
- Keep literal API keys, registry credentials, private keys, private datasets, and unpublished or private records out of manifests, repo files, Linear, and chat logs.

## Worker Roles

- **Mutating owner**: exactly one worker or orchestrator creates, verifies, and cleans up the RunPod resource.
- **Read-only monitor**: may run `list-pods`, `get-pod`, `runtime-metrics`, `cost-report`, `dashboard`, and Linear status comments. Stop, delete, and update operations are off limits.
- **Domain worker**: owns workload commands and artifact semantics. Cloud lifecycle is out of scope.
- **Orchestrator**: owns `run-handoff` or `run-remote`, launch locks, billing and cost closeout, and final cleanup proof.

## Minimum Preflight

For any paid run:

```bash
runpod-bridge validate-manifest <manifest>
runpod-bridge contract-self-check <manifest>
runpod-bridge preflight <manifest>
runpod-bridge prepare <manifest> --out-dir .runtime/<run-id>-packet
runpod-bridge validate-handoff .runtime/<run-id>-packet/provider_handoff.json
```

For long, expensive, large, or huge runs, also require:

```bash
runpod-bridge source-check <manifest> --execute
runpod-bridge egress-plan <manifest>
runpod-bridge profiles --recommend-for <manifest>
runpod-bridge productivity-plan <manifest>
```

The preflight must show a live productivity channel before paid launch: a sanitized `startup.progress.http_status_server_port` exposed through HTTP or TCP, or SSH/log tail for private workloads.

## First Paid Smoke Ladder

Run one tiny real smoke before the full job:

1. **Inline no-volume CPU smoke**: proves account create path, startup wrapper, artifact packet, and cleanup.
2. **Exact image canary**: proves bridge bootstrap prerequisites such as `git`, Python, shell, CUDA, or tool binaries are present before workload commands run.
3. **Volume canary**: proves network volume mount and file visibility when the real run needs retained storage.
4. **GPU canary**: proves scheduler, image, driver visibility, and `nvidia-smi` before running real GPU code.
5. **Full workload**: only after artifact egress, hashing, and cleanup have already been proven on the same route.

## Monitoring Truth

Record three layers:

- **Resource state**: RunPod pod fields, machine and data center, cost rate, public IP, ports, volume.
- **Runtime state**: GraphQL `runtime.uptimeInSeconds`, container CPU and memory, GPU utilization.
- **Workload state**: heartbeat file, status file, startup log, artifact hash ledger, and validation results.

Runtime metrics are mostly negative proof. Tiny or resetting uptime catches crash loops. A single non-zero CPU or GPU sample does not prove useful work. Live productivity needs a fresh `/healthz`, SSH tail, or fetched status/heartbeat/log packet that is advancing.

## Operator Tools

- Install `runpodctl` on trusted orchestrator hosts when possible. It improves SSH info lookup, billing fallback, and rendered `--terminate-after` create commands.
- Treat Flash and generic Serverless as separate adapter lanes. Route them through the pod runner only when the manifest names an implemented adapter and the bridge enforces deploy/job/output/undeploy gates.
