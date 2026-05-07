---
name: runpod-symphony
description: Guardrails for AI agents that launch paid workloads on RunPod from Linear issues. Validates the launch manifest, runs a local dry-run, gates remote pod creation on explicit authorization, requires SHA-256 artifact proof for success, and forces cleanup. Use when a Symphony, Codex, or Claude Code worker is about to create a RunPod pod, when a remote run is stuck, or when closing out a RunPod job with symphony-outcome.
---

# RunPod Symphony

Use this skill when an AI agent has authority to launch paid RunPod workloads. It blocks the three failure modes that show up when agents drive cloud spend:

- launching a pod without explicit authorization
- declaring success because the container exited cleanly, even when the workload produced nothing
- leaking API keys, credentials, or private data into manifests, logs, or Linear comments

The skill drives the `runpod-bridge` CLI. The agent reads a Linear issue or a launch manifest, validates the contract, runs a local dry-run, and only then creates a guarded pod. Success requires declared artifacts at declared paths with passing validation and SHA-256 hashes. Cleanup is part of the closeout.

The skill works for Codex workers, Claude Code workers, and mixed-agent Symphony lanes because the contract is a launch manifest plus the CLI. The bridge owns remote execution mechanics. Domain repos own workload commands and success criteria.

## When To Use

- A prompt mentions RunPod-backed Symphony or Linear execution, launch manifests, provider handoffs, remote smoke tests, artifact egress, pod cleanup, cost closeout, or `symphony-outcome`.
- A worker needs to decide whether it may mutate RunPod resources or should stop at a prepared handoff packet.
- A run is stuck, ambiguous, or risky and needs provider, runtime, and workload evidence separated before relaunch.
- A provider or neocloud hiccup should be turned into a reusable public-safe improvement rather than repeated as manual operator lore.
- A public-release pass needs the skill, examples, templates, and docs scrubbed for generated artifacts, private paths, or organization-specific assumptions.

## When Not To Use

- The task is a generic RunPod API question with no Symphony or Linear workload contract.
- The user wants domain-science interpretation, model-quality claims, or biological conclusions from artifacts. The domain repo owns that validation.
- The manifest or issue contains literal secrets, private data, unpublished sequences, customer process records, or generated run packets. Stop and ask for a sanitized contract or runtime references.

## Operating Rules

1. **Dry-run is the default.** Paid RunPod creation requires `remote_launch_allowed: true`, an explicit `launch_authorization` source, finite budget and runtime, an immutable repo reference, declared artifacts, validation commands, a cleanup policy, and a passing contract self-check.
2. **Success requires declared artifacts.** Pod RUNNING, container exit codes, and log presence do not close a run as success. Artifacts must exist at declared paths with SHA-256 hashes and passing validation commands.
3. **Secrets stay out of the contract.** API keys, registry credentials, private datasets, unpublished sequences, and customer process records belong in environment variables or runtime injection references. Manifests, templates, logs, and Linear comments carry only references.
4. **Cleanup is part of closeout.** Stop or delete the pod unless retention is explicitly approved and documented. If a network volume is attached, terminate the pod and preserve the volume rather than relying on stop semantics.

## Inputs To Establish

Before any plan or launch, identify:

- Linear issue URL or local launch manifest path.
- The authorization source for remote launch (issue field, manifest field, or operator instruction).
- The exact repo source: Git remote and immutable commit, snapshot path, or another reproducible reference.
- Workload commands, validation commands, and expected artifact paths from the domain repo.
- Compute profile, image or template, data center or GPU requirements, volume policy, and runtime budget.
- Monitoring plan: poll cadence, heartbeat or status files, log path, artifact egress path, and silence timeout.
- Cleanup policy: stop, delete, or approved retention.
- Secret and private-data policy: runtime injection or secure-store references only.

## Default Workflow

1. Read the Linear issue or launch manifest. Identify the authorization source for remote launch.
2. Scrub inputs before execution. Reject literal secrets, private data, unpublished sequences, raw customer records, or registry credentials.
3. Normalize the contract into a launch manifest. Start from `assets/templates/runpod-launch-manifest.template.json` if one does not exist.
4. Validate locally. Run `runpod-bridge validate-manifest`, `contract-self-check`, `plan`, and `prepare`. The contract self-check should prove input materialization, exact tool invocation, artifact validation, and claim boundaries.
5. Render or inspect the startup command without launching paid resources. Confirm it runs only the declared workload and validation commands. Run `preflight` to check rendered POST body size. Payloads near 48KB are risky. Payloads above the bridge hard limit must be compressed or moved to a repo, snapshot, or object-store handoff.
6. For long, expensive, large, or huge runs, require `productivity-plan` to show a live productivity channel before paid launch. Provider RUNNING, billing records, GraphQL utilization, and completion-only artifact servers do not satisfy this gate.
7. Before the first paid workload on a new route, run the smallest real smoke that proves create, boot, progress signal, artifact egress, hashing, and cleanup. The smoke ladder lives in `references/worker-readiness.md`.
8. Stop at a dry-run plan unless the launch gate is satisfied: `remote_launch_allowed: true`, explicit `launch_authorization`, budget and time limits, cleanup policy, expected artifacts, validation commands, passing contract self-check, and exact repo source.
9. After the launch gate passes, create or start RunPod resources from the declared manifest. Record pod ID, image or template, data center, volumes, ports, start time, and startup command source.
10. Monitor resource state, runtime health, workload state, and a peek channel. Poll `get_pod` with machine and network volume details. Sample `runtime-metrics` to catch crash loops from low or resetting `uptimeInSeconds`. Require workload-written heartbeat, status, and log files, since provider runtime metrics alone do not prove productivity. For live visibility, use `startup.progress.http_status_server_port` for sanitized smokes or SSH and log tail for private workloads.
11. Capture logs and artifacts. Compute SHA-256 hashes for declared artifacts, scan live text artifacts for the forbidden markers in `references/contract-checklist.md`, and run validation commands.
12. When workspace archive egress is declared, require the archive packet as part of closeout evidence.
13. Enforce cleanup. Stop or delete the pod unless retention is approved. If a network volume is attached, terminate the pod and preserve the volume.
14. Close out with a parseable `symphony-outcome` block based on `assets/templates/symphony-outcome.md`.

## Run Choice

- `run-handoff`: a Symphony worker prepared `provider_handoff.json` and a trusted orchestrator owns the paid mutation. Useful when the worker shell cannot reach RunPod directly.
- `run-remote`: the orchestrator owns the launch manifest directly and creates, verifies, and cleans up in one audited record.

## Tooling Guidance

### Happy path

- Prefer `runpod-bridge` on `PATH`. In a source checkout, use `bin/runpod-bridge`.
- Sequence: `validate-manifest` → `contract-self-check` → `prepare` → `validate-handoff` → `run-handoff` or `run-remote` → `closeout`.
- Mutating runs hold a local launch lock. Set `RUNPOD_BRIDGE_LOCK_DIR` or `--lock-dir` when multiple orchestrators should share one lock path.
- One mutating worker per run. Monitoring workers stay read-only and report through Linear or local status files.
- Use Linear tools only for issue intake, status updates, and outcome comments requested by the user or workflow.

### Worker sandbox constraints

Codex and Claude Code worker sandboxes may have no outbound DNS or TCP. Run `runpod-bridge list-pods --name-prefix <expected-prefix> --json` as a preflight before remote mutation. If it fails, stop at a prepared launch packet, validate `provider_handoff.json`, and hand off `run-handoff` plus Linear closeout to an unsandboxed orchestrator or trusted `after_run` hook.

### Productivity proof for long or expensive runs

- Run `contract-self-check`, `source-check --execute`, `preflight`, `egress-plan`, `profiles --recommend-for`, and `productivity-plan` before paid launch.
- After launch, sample `runtime-metrics` early and again after a few minutes. Use `supervise`, `cost-report`, and `dashboard` during closeout.
- A live productivity channel (sanitized `/healthz`, SSH or log tail, or another fetchable status packet) is required before paid launch. Provider RUNNING, billing records, runtime utilization, and completion-only artifact servers do not satisfy this gate.

### Payload and image limits

- `preflight` warns when the rendered POST body approaches 48KB and blocks above the hard limit. Compress large inline material with gzip and base64, or move it into a repo, snapshot, packet file, network volume, or object store.
- For `repo.source: git_remote_or_snapshot`, prove the pod image has `git` before paid launch. The bridge clones before `startup.commands` runs, so installing git inside `startup.commands` is too late. Declare `runpod.image_capabilities: ["git"]` only after verifying the exact image, or use inline, snapshot, or object-store bootstrap.

### Provider quirks

- Treat negative `runtime.uptimeInSeconds` as invalid telemetry or an unhealthy pod agent. Require `/healthz`, SSH and log tail, or fetched artifact proof before continuing spend. If GraphQL uptime goes negative and declared HTTP proxy paths stay 404, clean up and retry with a tiny provider smoke on a different scheduling pool.
- For GPU failures, isolate provider scheduling from workload code with a tiny image-native smoke such as `nvidia-smi` plus a minimal HTTP server. Escalate GPU family only after that smoke proves the workload and image path are working.
- For HTTP artifact and progress smokes, probe `https://<pod-id>-<internal-port>.proxy.runpod.net/...` directly as soon as the service should be listening. REST `publicIp` and `portMappings` can lag the working HTTP proxy path.
- Use multi-flavor CPU fallback for tiny CPU smokes when the workload is portable, such as `cpu5g` plus `cpu5c`, instead of pinning one fragile flavor. Treat fast second launches as best-case warm-image cache timing.
- See `references/failure-playbook.md` for the full symptom-to-action table.

### Egress

- HTTP proxy and direct TCP packet verification are inspection aids for short-lived sanitized smoke artifacts. Private or production workloads should use workspace archives plus SCP, network volume, presigned S3 upload, or object-store egress for durable artifact proof.
- For AWS artifact egress, prefer `aws_s3_presigned_upload` when the pod should not receive AWS credentials. Use `object_store_upload` only when runtime-injected AWS-compatible credentials and CLI behavior are part of the contract.
- For AWS-backed orchestration, run `aws-orchestrator-plan` first. It renders STS, RunPod network-volume S3, ECR registry refresh, Secrets Manager, SQS, DynamoDB lock, and EventBridge cleanup templates without executing them.

### Adapter lanes

RunPod Flash, generic Serverless endpoints, and Instant Clusters are separate adapter lanes. Route them through the pod runner only when the manifest names an implemented adapter.

When `runpodctl` is installed, use `pod-ssh-info` for SSH connection details and billing commands with `--backend runpodctl` for read-only operator-side billing checks. Treat `budget.terminate_after_minutes` as a platform-side backstop only when using `runpodctl pod create`. The REST pod runner records the value but still depends on bridge cleanup.

### Authorization boundary

When remote launch is not authorized, produce a concrete plan and blocker list rather than launching. Before publishing the skill or bridge, run `public-audit` from the source checkout and treat any failure as a release blocker.

## Local Checks

These commands run without `RUNPOD_API_KEY` or paid RunPod resources:

`doctor`, `validate-manifest`, `contract-self-check`, `plan`, `prepare`, `render-startup`, `render-runpodctl-create`, `aws-orchestrator-plan`, `productivity-plan`, `source-check` (without `--execute`), `run-local`, `monitor`, `supervise`, `closeout`, `public-audit`, `validate-linear-issue`, `issue-intake`, `egress-plan`, `profiles`, and `provider-capabilities`.

## Reference Files

Read these when the task surface is wider than the happy path.

- `references/runpod-operations.md`: enabling RunPod access, SSH and SCP, port exposure, monitoring, cost reporting, and worker coordination.
- `references/cli-reference.md`: full command inventory.
- `references/worker-readiness.md`: read before assigning RunPod work to Symphony Codex or Claude Code workers, especially when deciding whether a worker can mutate cloud resources or should produce a handoff packet only.
- `references/failure-playbook.md`: read when pod create, boot, proxy, runtime metrics, git bootstrap, GPU scheduling, or artifact fetch behavior is ambiguous.
- `references/contract-checklist.md`: required manifest fields, stage contract proof, launch gate, secret screening, and closeout requirements.

In this repo, also see:

- `docs/runpod-superpowers-2026-05.md` before changing provider strategy around Flash, Serverless endpoints, `runpodctl`, cost centers, or Instant Clusters.
- `docs/runpod-observability-ladder.md` before changing monitoring, stuck-pod diagnosis, SSH peek behavior, or progress endpoints.
- `docs/neocloud-self-learning-runbook.md` after provider hiccups, failed smokes, manual rescues, or cost surprises that should become reusable checks or examples.
- `docs/aws-runpod-superpowers.md` before changing AWS companion behavior such as S3 egress, ECR registry auth, SQS handoff queues, or orchestrator-side AWS locks and backstops.

## Artifact Packet

Create or expect a packet shaped like:

```text
runpod-execution/
  launch_manifest.json
  provider_handoff.json
  startup.sh
  local_preflight.json
  monitor_events.ndjson
  status.json
  runpod_resource_record.json
  logs/
  artifacts/
  artifact_hashes.json
  closeout.json
  symphony_outcome.md
```

## Generic Use

The bridge stays reusable across Symphony and Linear setups. Domain workloads pass their contracts into the skill instead of being hard-coded.

Before public release, run `runpod-bridge public-audit` and scrub organization-specific assumptions from code, examples, references, logs, and skill assets.
