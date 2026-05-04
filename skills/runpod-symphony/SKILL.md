---
name: runpod-symphony
description: Use for Symphony + Linear RunPod work: launch manifests, provider handoffs, local dry-runs, guarded remote runs, artifact proof, cleanup, and symphony-outcome closeout.
---

# RunPod Symphony

Use this skill to run repo-defined workloads on RunPod with Symphony as the dispatcher and Linear as the work ledger. It works for Codex workers, Claude Code workers, and mixed-agent Symphony lanes because the contract is a launch manifest plus the `runpod-bridge` CLI. Own the remote execution mechanics; leave domain science, model interpretation, and workload-specific success criteria in the domain repo.

## When To Use

- A prompt mentions RunPod-backed Symphony/Linear execution, launch manifests, provider handoffs, remote smoke tests, artifact egress, pod cleanup, cost closeout, or `symphony-outcome`.
- A worker needs to decide whether it may mutate RunPod resources or should stop at a prepared handoff packet.
- A run is stuck, ambiguous, or risky and needs provider/runtime/workload evidence separated before relaunch.
- A public-release pass needs the skill, examples, templates, and docs checked for generated artifacts, private paths, or organization-specific assumptions.

## When Not To Use

- The task is only a generic RunPod API question with no Symphony/Linear workload contract.
- The user wants domain-science interpretation, model-quality claims, or biological conclusions from artifacts. The domain repo owns that validation.
- The manifest or issue contains literal secrets, private data, unpublished sequences, private customer/process records, or generated run packets. Stop and ask for a sanitized contract or secure runtime references.

## Operating Rules

- Default to local dry-run validation until the operator or Linear issue explicitly authorizes remote launch.
- Do not create paid RunPod resources unless `remote_launch_allowed: true`, budget/time limits, cleanup policy, validation commands, and expected artifacts are all present.
- Never store API keys, registry credentials, private datasets, unpublished sequences, customer process records, or other sensitive values in repo files, templates, logs, or Linear comments.
- Treat RunPod as execution, not orchestration. Linear owns authorization and audit trail; Symphony owns dispatch; the domain repo owns workload logic.
- Do not claim success from pod creation, pod start, command exit, or log presence alone. Success requires declared artifact checks.
- Require paid launches to pass the stage contract checklist in `references/contract-checklist.md`.
- Stop or delete pods at closeout unless retention is explicitly approved and documented.
- Keep the workflow domain-agnostic; domain workloads pass contracts into this bridge.

## Inputs To Establish

- Linear issue URL or local launch manifest path.
- Explicit launch authorization source when remote creation is requested.
- Exact repo source: Git remote and commit, local snapshot path, or other immutable source reference.
- Workload commands, validation commands, and expected artifact paths from the domain repo.
- Compute profile, image/template, data center or GPU requirements, volume policy, and runtime budget.
- Monitoring plan: pod polling cadence, heartbeat/status files, log file path, artifact egress path, and silence timeout.
- Cleanup policy: stop, delete, or explicitly approved retention.
- Secret and private-data policy: runtime injection or secure-store references only, never literal values.

## Default Workflow

1. Read the Linear issue or launch manifest and identify the source of authority for remote launch.
2. Scrub inputs before execution. Reject or redact literal secrets, private data, unpublished sequences, raw customer records, or registry credentials.
3. Normalize the contract into a launch manifest. If one does not exist, start from `assets/templates/runpod-launch-manifest.template.json`.
4. Validate locally first. Prefer repo tooling such as `runpod-bridge validate-manifest`, `runpod-bridge contract-self-check`, `runpod-bridge plan`, and `runpod-bridge prepare` when available; otherwise use the checklist in `references/contract-checklist.md`. The contract self-check should prove input materialization, exact tool invocation, artifact validation, and claim boundaries.
5. Render or inspect the startup command without launching paid resources. Confirm it runs only the declared workload and validation commands. Check rendered RunPod create payload size through `preflight`; large inline `dockerStartCmd` payloads near 48KB are risky, and payloads above the bridge hard limit must be compressed or moved to a repo/snapshot/object-store handoff before paid launch.
6. For any long, expensive, large, or huge run, require `productivity-plan` to show a live productivity channel before paid launch. Provider `RUNNING`, billing records, GraphQL utilization, and completion-only artifact servers do not satisfy this gate.
7. Before the first real paid workload on a new route, run the smallest real smoke that proves create, boot, progress signal, artifact egress, hashing, and cleanup. Use the smoke ladder in `references/worker-readiness.md` when choosing CPU, exact-image, volume, or GPU canaries.
8. Stop at a dry-run plan unless the launch gate is satisfied: `remote_launch_allowed: true`, explicit `launch_authorization`, budget/time limits, cleanup policy, expected artifacts, validation commands, passing contract self-check, and exact repo source.
9. After the launch gate passes, create or start RunPod resources using the declared manifest. Record pod ID, image/template, data center, volumes, ports, start time, and startup command source.
10. Monitor resource state, runtime health, workload state, and a peek channel. Poll `get_pod` with machine and network volume details, then use `runtime-metrics` to catch crash loops from low or resetting `uptimeInSeconds`. Require workload-written heartbeat/status/log files for execution progress because provider runtime metrics do not prove productivity and MCP does not currently provide pod log streaming or in-pod exec. For live visibility, prefer `startup.progress.http_status_server_port` for sanitized smokes or SSH/log tail for private long-running workloads.
11. Capture logs and artifacts. Compute SHA-256 hashes for declared artifacts, scan live text artifacts for the forbidden markers listed in `references/contract-checklist.md`, and run validation commands.
12. When workspace archive egress is declared, require the archive packet as part of closeout evidence.
13. Enforce cleanup. Stop or delete the pod unless retention is approved in the issue or manifest; if a network volume is attached, terminate the pod and preserve the volume rather than assuming `stop` is available.
14. Close out with a parseable `symphony-outcome` block based on `assets/templates/symphony-outcome.md`.

## Tooling Guidance

- Prefer `runpod-bridge` on `PATH`; in a source checkout, use `bin/runpod-bridge`.
- Happy path: `validate-manifest` -> `contract-self-check` -> `prepare` -> `validate-handoff` -> `run-handoff` or `run-remote` -> `closeout`.
- For mutating runs, keep the default local launch lock enabled. Set `RUNPOD_BRIDGE_LOCK_DIR` or pass `--lock-dir` when multiple Symphony orchestrators should coordinate through a shared lock path.
- In Symphony Codex or Claude Code worker sandboxes, do not assume shell networking works. Run a cheap `runpod-bridge list-pods --name-prefix <expected-prefix> --json` preflight before remote mutation. If DNS/TCP fails, have the worker stop at a prepared launch packet, validate `provider_handoff.json`, and hand off `run-handoff` plus Linear closeout to an unsandboxed orchestrator or trusted `after_run` hook.
- Use only one mutating worker per RunPod run. Monitoring workers must stay read-only and report through Linear or local status files.
- Use Linear tools only for issue intake, status updates, and outcome comments requested by the user or workflow.
- If command execution or log streaming inside the pod is unavailable, use startup-command execution for V1 and state the limitation in the outcome.
- Use RunPod REST billing history when available for final cost; otherwise mark cost as an estimate derived from runtime and pod cost fields.
- When `runpodctl` is installed, use `pod-ssh-info` for SSH connection details and billing commands with `--backend runpodctl` for read-only operator-side billing checks.
- Treat `budget.terminate_after_minutes` as a platform-side backstop only for `runpodctl pod create`; the REST pod runner records it but still depends on bridge cleanup.
- Treat RunPod Flash, generic Serverless endpoints, and Instant Clusters as separate adapter lanes. Do not force them through the pod runner or claim support until the manifest names an implemented adapter.
- For inline public smokes, prefer compressed gzip/base64 payload material or a prepared packet over very large literal startup scripts. The RunPod REST docs do not document a `dockerStartCmd` request-size ceiling, but live testing showed a failure near 65KB; the bridge preflight now warns near 48KB and blocks above its hard limit.
- For `repo.source: git_remote_or_snapshot`, prove the pod image has `git` before paid launch. `startup.commands` run after bridge repo bootstrap, so installing git there is too late. Declare `runpod.image_capabilities: ["git"]` only after verifying the exact image or use inline/snapshot/object-store bootstrap.
- For HTTP artifact/progress smokes, probe `https://<pod-id>-<internal-port>.proxy.runpod.net/...` directly as soon as the service should be listening. REST `publicIp` and `portMappings` can lag the working HTTP proxy path, so do not treat empty REST port fields as proof the proxy is unavailable.
- Use multi-flavor CPU fallback for tiny CPU smokes when the workload is portable, such as `cpu5g` plus `cpu5c`, instead of pinning one fragile CPU flavor. Do not budget from warm-image cache timing; treat fast second launches as best case.
- Treat negative `runtime.uptimeInSeconds` as invalid provider telemetry or an unhealthy RunPod pod agent, not as useful progress. If GraphQL uptime goes negative and declared HTTP proxy paths stay 404, require immediate SSH/log/artifact proof or clean up and retry with a tiny provider smoke on a different scheduling pool.
- For GPU failures, first isolate provider scheduling from workload code with a tiny image-native smoke such as `nvidia-smi` plus a minimal HTTP server. Only escalate to a more expensive GPU family retry after that smoke proves the workload/image path is not the blocker.
- For long or expensive jobs, run `contract-self-check`, `source-check --execute`, `preflight`, `egress-plan`, `profiles --recommend-for`, and `productivity-plan` before paid launch. After launch, sample `runtime-metrics` early and again after a few minutes, then use `supervise`, `cost-report`, and `dashboard` during closeout.
- If `productivity-plan` reports only provider state, runtime metrics, and completion-only artifact inspection, the agent cannot honestly tell whether the pod is productive until a heartbeat/status/log packet, live `/healthz`, or SSH tail is reachable. Runtime metrics can prove likely crash-loop or recent restart, but not success.
- Use HTTP proxy or direct TCP packet verification only for short-lived, sanitized smoke artifacts; prefer SCP, network volume, or object-store upload for private or production artifact proof.
- For AWS artifact egress, prefer `aws_s3_presigned_upload` when the pod should not receive AWS credentials. Use `object_store_upload` only when runtime-injected AWS-compatible credentials and CLI behavior are explicitly part of the contract.
- For AWS-backed orchestration, run `aws-orchestrator-plan` first. It should render STS, RunPod network-volume S3, ECR registry refresh, Secrets Manager, SQS, DynamoDB lock, and EventBridge cleanup templates without executing them.
- When remote launch is not authorized, produce a concrete plan and blocker list instead of launching.
- When preparing this skill or bridge for public release, run `public-audit` from the source checkout and treat failures in source/tests/bin scanning, repo-local skill linkage, template sync, generated packets, or local-private path markers as release blockers.

## Run Choice

- Use `run-handoff` when a Symphony worker prepared `provider_handoff.json`, especially when worker DNS/API reachability is blocked and a trusted orchestrator owns paid mutation.
- Use `run-remote` when the orchestrator owns the launch manifest directly and should create, verify, and clean up in one audited record.

## Local Checks

These commands do not require `RUNPOD_API_KEY` or paid RunPod resources: `doctor`, `validate-manifest`, `contract-self-check`, `plan`, `prepare`, `render-startup`, `render-runpodctl-create`, `aws-orchestrator-plan`, `productivity-plan`, `source-check` without `--execute`, `run-local`, `monitor`, `supervise`, `closeout`, `public-audit`, `validate-linear-issue`, `issue-intake`, `egress-plan`, `profiles`, and `provider-capabilities`.

Read `references/runpod-operations.md` when a task involves enabling RunPod access, SSH/SCP, port exposure, monitoring, cost reporting, or worker coordination.

Read `references/cli-reference.md` when you need the full command inventory.

Read `references/worker-readiness.md` before assigning RunPod work to Symphony Codex or Claude Code workers, especially when deciding whether a worker can mutate cloud resources or should produce a handoff packet only.

Read `references/failure-playbook.md` when pod create, boot, proxy, runtime metrics, git bootstrap, GPU scheduling, or artifact fetch behavior is ambiguous.

In this repo, read `docs/runpod-superpowers-2026-05.md` before changing provider strategy around Flash, Serverless endpoints, `runpodctl`, cost centers, or Instant Clusters.

In this repo, read `docs/runpod-observability-ladder.md` before changing monitoring, stuck-pod diagnosis, SSH peek behavior, or progress endpoints.

In this repo, read `docs/aws-runpod-superpowers.md` before changing AWS companion behavior such as S3 egress, ECR registry auth, SQS handoff queues, or orchestrator-side AWS locks/backstops.

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

Keep the bridge reusable for any Symphony + Linear setup. Domain workloads should pass contracts into this skill rather than becoming hard-coded behavior.

Before public release, run `runpod-bridge public-audit` and scrub organization-specific assumptions from code, examples, references, logs, and skill assets.
