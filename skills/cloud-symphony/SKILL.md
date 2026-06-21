---
name: cloud-symphony
description: "Use for Symphony + Linear cloud-compute work: launch manifests, provider setup guidance, local dry-runs, guarded remote runs where supported, artifact proof, cleanup, self-learning ledger, and symphony-outcome closeout."
---

# Cloud Symphony

Use this skill to help agents run repo-defined workloads on cloud compute with Symphony as the dispatcher and Linear as the work ledger. The repo provides provider setup guidance, launch manifests, local preflights, handoff packets, artifact proof, cleanup rules, and automated launch support where the bridge has a guarded provider path. Consumer Google Colab is intentionally not a lane (no API; ToS bans headless use). Own the remote execution mechanics; leave domain science, model interpretation, and workload-specific success criteria in the domain repo. See `docs/provider-adapter-contract.md` and `docs/providers/`.

## Operating Rules

- Default to local dry-run validation until the operator or Linear issue explicitly authorizes remote launch.
- Do not create paid RunPod resources unless `remote_launch_allowed: true`, budget/time limits, cleanup policy, validation commands, and expected artifacts are all present.
- Record billing attribution before paid launch with `billing.cost_center`, `billing.project_code`, and `billing.resource_owner` when those values are known.
- Never store API keys, registry credentials, private datasets, unpublished sequences, customer process records, or other sensitive values in repo files, templates, logs, or Linear comments.
- Treat RunPod as execution, not orchestration. Linear owns authorization and audit trail; Symphony owns dispatch; the domain repo owns workload logic.
- Do not claim success from pod creation, pod start, command exit, or log presence alone. Success requires declared artifact checks.
- Treat provider `RUNNING`, public HTTP/TCP reachability, and monitor heartbeats as non-authoritative unless they advance declared workload state. Public endpoints require sanitized artifacts or explicit application-layer authentication.
- Require paid launches to pass the stage contract checklist in `references/contract-checklist.md`.
- Stop or delete pods at closeout unless retention is explicitly approved and documented.
- Keep the workflow domain-agnostic; domain workloads pass contracts into this bridge.

## Inputs To Establish

- Linear issue URL or local launch manifest path.
- Explicit launch authorization source when remote creation is requested.
- Exact repo source: Git remote and commit, local snapshot path, or other immutable source reference.
- Source and image visibility: public-sanitized smoke only, private Git with runtime auth, prepared snapshot URL, mounted network-volume snapshot, or private registry auth.
- Cloud trust tier: default to RunPod Secure Cloud. Community Cloud requires an explicit public/synthetic sanitized-smoke opt-in and must not carry private source, secrets, durable volumes, unpublished data, or production claims.
- Workload commands, validation commands, and expected artifact paths from the domain repo.
- Compute profile, image/template, data center or GPU requirements, volume policy, and runtime budget.
- Monitoring plan: pod polling cadence, heartbeat/status files, log file path, artifact egress path, and silence timeout.
- Cleanup policy: stop, delete, or explicitly approved retention.
- Secret and private-data policy: runtime injection or secure-store references only, never literal values.

## Default Workflow

1. Read the Linear issue or launch manifest and identify the source of authority for remote launch.
2. Scrub inputs before execution. Reject or redact literal secrets, private data, unpublished sequences, raw customer records, or registry credentials.
3. Normalize the contract into a launch manifest. If one does not exist, start from `assets/templates/runpod-launch-manifest.template.json`.
4. Validate locally first. Prefer repo tooling such as `cloud-bridge audit-manifests`, `cloud-bridge validate-manifest`, `cloud-bridge contract-self-check`, `cloud-bridge plan`, and `cloud-bridge prepare` when available; otherwise use the checklist in `references/contract-checklist.md`. The contract self-check should prove input materialization, exact tool invocation, artifact validation, and claim boundaries.
5. Render or inspect the startup command without launching paid resources. Confirm it runs only the declared workload and validation commands. Check rendered RunPod create payload size through `preflight`; large inline `dockerStartCmd` payloads near 48KB are risky, and payloads above the bridge hard limit must be compressed or moved to a repo, prepared snapshot, or object-store handoff before paid launch.
6. For analytical, scientific, fanout, volume-backed, GPU-heavy, or multi-stage workloads, apply `references/provider-backed-workload-lessons.md`: primary/context/dossier evidence lanes, cardinality gate, exact executable proof, normalized ledgers, no silent fallback, partial summary, and claim boundaries.
7. For any long, expensive, large, or huge run, require `productivity-plan` to show a live productivity channel before paid launch. Provider `RUNNING`, billing records, GraphQL utilization, and completion-only artifact servers do not satisfy this gate.
8. Before the first real paid workload on a new route, run the smallest real smoke that proves create, boot, progress signal, artifact egress, hashing, and cleanup. Use the smoke ladder in `references/worker-readiness.md` when choosing CPU, exact-image, volume, or GPU canaries. For GPU routes with explicit `gpuTypeIds`, run `gpu-catalog --manifest <manifest>` before any REST retry loop so "no instances" is not confused with wrong-DC catalog mismatch.
9. Stop at a dry-run plan unless the launch gate is satisfied: `remote_launch_allowed: true`, explicit `launch_authorization`, budget/time limits, cleanup policy, expected artifacts, validation commands, passing contract self-check, and exact repo source.
10. After the launch gate passes, create or start RunPod resources using the declared manifest. Record pod ID, image/template, data center, volumes, ports, start time, and startup command source.
11. Monitor resource state, runtime health, workload state, and a peek channel. Poll `get_pod` with machine and network volume details, then use `runtime-metrics` to catch crash loops from low or resetting `uptimeInSeconds`. Use `progress-report --previous ... --out ...` for monitor loops so repeated samples prove advancement before agents report workload progress. Require workload-written heartbeat/status/log files for execution progress because provider `RUNNING`, runtime metrics, HTTP/TCP reachability, and bridge monitor heartbeat do not prove productivity, and MCP does not currently provide pod log streaming or in-pod exec. For live visibility, prefer `startup.progress.http_status_server_port` for sanitized smokes or SSH/log tail for private long-running workloads.
12. Capture logs and artifacts. Compute SHA-256 hashes for declared artifacts, scan live text artifacts for the forbidden markers listed in `references/contract-checklist.md`, and run validation commands.
13. When workspace archive egress is declared, require the archive packet as part of closeout evidence.
14. Enforce cleanup. Stop or delete the pod unless retention is approved in the issue or manifest; if a network volume is attached, terminate the pod and preserve the volume rather than assuming `stop` is available.
15. Close out with a parseable `symphony-outcome` block based on `assets/templates/symphony-outcome.md`.

## Tooling Guidance

- Prefer `cloud-bridge` on `PATH`; in a source checkout, use `bin/cloud-bridge`.
- Happy path: `audit-manifests` -> `validate-manifest` -> `contract-self-check` -> `prepare` -> `validate-handoff` -> `run-handoff` or `run-remote` -> `remote-outcome`.
- For mutating runs, keep the default local launch lock enabled. Set `RUNPOD_BRIDGE_LOCK_DIR` or pass `--lock-dir` when multiple Symphony orchestrators should coordinate through a shared lock path.
- In Symphony/Codex worker sandboxes, do not assume shell networking works. Run a cheap `cloud-bridge list-pods --name-prefix <expected-prefix> --json` preflight before remote mutation. If DNS/TCP fails, have the worker stop at a prepared launch packet, validate `provider_handoff.json`, and hand off `run-handoff` plus Linear closeout to an unsandboxed orchestrator or trusted `after_run` hook.
- Use only one mutating worker per RunPod run. Monitoring workers must stay read-only and report through Linear or local status files.
- Use Linear tools only for issue intake, status updates, and outcome comments requested by the user or workflow.
- If command execution or log streaming inside the pod is unavailable, use startup-command execution for V1 and state the limitation in the outcome.
- Use RunPod REST billing history when available for final cost; otherwise mark cost as an estimate derived from runtime and pod cost fields.
- When `runpodctl` is installed, use `pod-ssh-info` for SSH connection details and billing commands with `--backend runpodctl` for read-only operator-side billing checks.
- Treat `budget.terminate_after_minutes` as a platform-side backstop only for `runpodctl pod create`; the REST pod runner records it but still depends on bridge cleanup.
- Treat RunPod Flash, generic Serverless endpoints, and Instant Clusters as separate adapter lanes. Do not force them through the pod runner or claim support until the manifest names a provider path with automated launch support.
- For inline public smokes, prefer compressed gzip/base64 payload material or a prepared packet over very large literal startup scripts. The RunPod REST docs do not document a `dockerStartCmd` request-size ceiling, but live testing showed a failure near 65KB; the bridge preflight now warns near 48KB and blocks above its hard limit.
- For `repo.source: git_remote`, prove the pod image has `git` before paid launch. `startup.commands` run after bridge repo bootstrap, so installing git there is too late. Declare `runpod.image_capabilities: ["git"]` only after verifying the exact image or use inline, prepared_snapshot, or object-store bootstrap.
- For private source, prefer `prepared_snapshot` with `archive_url_ref` or `archive_pod_path` over public GitHub. Run `source-ingress-plan` when staging source through a RunPod network volume and S3-compatible API.
- For private registry images, configure provider-side RunPod registry auth or prove `runpod.image_pull_verified: true` with an exact image-pull canary. Local Docker credentials, GitHub auth, or ECR login state on the worker do not prove RunPod can pull the image.
- For HTTP artifact/progress smokes, probe `https://<pod-id>-<internal-port>.proxy.runpod.net/...` directly as soon as the service should be listening. REST `publicIp` and `portMappings` can lag the working HTTP proxy path, so do not treat empty REST port fields as proof the proxy is unavailable.
- For monitor loops, report four separate facts: monitor liveness, provider runtime health, workload advancement, and artifact proof. A periodic monitor heartbeat is not a workload heartbeat. A fresh `/healthz` sample is not workload progress unless it advances from the previous `progress-report`.
- In every monitor update, copy `classification.state`, `classification.workload_progressing`, `classification.monitor_alive`, `classification.outage_suspected`, and `classification.next_action` exactly from `progress-report`; do not translate them into green/healthy/progress/outage wording.
- Use multi-flavor CPU fallback for tiny CPU smokes when the workload is portable, such as `cpu5g` plus `cpu5c`, instead of pinning one fragile CPU flavor. Do not budget from warm-image cache timing; treat fast second launches as best case.
- For GPU "no instances" or "machine does not have resources" failures, probe the catalog first with `gpu-catalog --manifest <manifest> --json`. REST can return the same error for a GPU type that is not offered in that data center and for a valid GPU type with zero current capacity. Retrying only helps the second case.
- Treat negative `runtime.uptimeInSeconds` as invalid provider telemetry or an unhealthy RunPod pod agent, not as useful progress. If GraphQL uptime goes negative and declared HTTP proxy paths stay 404, require immediate SSH/log/artifact proof or clean up and retry with a tiny provider smoke on a different scheduling pool.
- For GPU failures, first isolate provider scheduling from workload code with a tiny image-native smoke such as `nvidia-smi` plus a minimal HTTP server. Only escalate to a more expensive GPU family retry after that smoke proves the workload/image path is not the blocker.
- For batch pods that must be inspected after completion, prefer `startup.terminal_hold.mode: sleep_infinity` with orchestrator cleanup. The workload should write final `status.json`, logs, hashes, and artifacts, then idle instead of exiting so RunPod cannot blur a clean terminal state into a restart cycle.
- For long or expensive jobs, run `audit-manifests`, `contract-self-check`, `source-check --execute`, `preflight`, `egress-plan`, `profiles --recommend-for`, and `productivity-plan` before paid launch. After launch, sample `runtime-metrics` early and again after a few minutes, run repeated `progress-report` samples, then use `supervise`, `cost-report`, `remote-outcome`, and `dashboard` during closeout.
- For Spot or interruptible pods, require non-`none` checkpoint policy, explicit resume/rerun policy, and durable artifact egress before paid launch.
- If `productivity-plan` reports only provider state, runtime metrics, and completion-only artifact inspection, the agent cannot honestly tell whether the pod is productive until a heartbeat/status/log packet, live `/healthz`, or SSH tail is reachable. Runtime metrics can prove likely crash-loop or recent restart, but not success.
- Use HTTP proxy or direct TCP packet verification only for short-lived, sanitized smoke artifacts; prefer SCP, network volume, or object-store upload for private or production artifact proof.
- For AWS artifact egress, prefer `aws_s3_presigned_upload` when the pod should not receive AWS credentials. Use `object_store_upload` only when runtime-injected AWS-compatible credentials and CLI behavior are explicitly part of the contract.
- For AWS-backed orchestration, run `aws-orchestrator-plan` first. It should render STS, RunPod network-volume S3, ECR registry refresh, Secrets Manager, SQS, DynamoDB lock, and EventBridge cleanup templates without executing them.
- When remote launch is not authorized, produce a concrete plan and blocker list instead of launching.

## Self-Learning and Escalation

The bridge keeps a durable, append-only learnings ledger so agents do not re-learn provider lessons. Read `references/self-learning.md` for the full protocol. The loop:

- Before escalating or asking the operator, `learnings search --provider <name> --query "<symptom>"` and read the provider's `known_patterns` (`provider-capabilities <name>`) and `learnings_doc`.
- Record an issue the moment you hit it: `learnings record --provider <name> --category <cat> --symptom "<one line>"`; when you find the fix, record a `--status resolved` entry with `--resolution` and an `--evidence` citation.
- When stuck after a couple of bounded attempts, or on any novel critical money/cleanup ambiguity, launch a research sub-agent: `learnings brief --provider <name> --symptom "<symptom>" --json` renders the payload (prior learnings, provider entry `known_patterns`, doc links, suggested queries, and an `agent_instruction`). Hand it to a Claude Agent or Symphony research worker, then record what it finds. The sub-agent reads the bundled knowledge, searches official docs and the recent web, and must not run paid or mutating actions.
- At closeout, `learnings promote` lists scrub-clean resolved learnings as ready-to-paste `known_patterns` bullets for the provider entry; edit the provider entry, then `learnings promote --mark <id>`.

The ledger defaults to the gitignored `internal/private/learnings/`, and `record` scrub-checks every entry, so raw run context never reaches the public-readiness scan and only scrub-clean resolved learnings are promotable into public docs.

## Run Choice

- Use `run-handoff` when a Symphony worker prepared `provider_handoff.json`, especially when worker DNS/API reachability is blocked and a trusted orchestrator owns paid mutation.
- Use `run-remote` when the orchestrator owns the launch manifest directly and should create, verify, and clean up in one audited record.
- Use `run-job` for a Hugging Face Job (`provider.name: huggingface`): it submits a one-shot container, polls to a terminal stage, verifies the artifacts the job pushed to a Hub repo (`hf_hub_repo` egress), and cancels on abort — the batch-provider analog of `run-remote`. Needs `HF_TOKEN` and a positive HF credit balance for `--execute` (just add a card — **no Pro/Team/Enterprise subscription required**; gated by `--yes-run-paid-hf-job`); dry-run renders the submit request with no API call. Close out the same as RunPod: `closeout` then `remote-outcome` (provider-aware) render the Linear-ready symphony-outcome block from the HF run record (`provider: huggingface`, `job_id`, flavor, runtime, cost, egress).

## Local Checks

These commands do not require `RUNPOD_API_KEY` or paid RunPod resources: `doctor`, `audit-manifests`, `validate-manifest`, `contract-self-check`, `plan`, `prepare`, `render-startup`, `render-runpodctl-create`, `aws-orchestrator-plan`, `productivity-plan`, `source-check` without `--execute`, `source-ingress-plan`, `run-local`, `monitor`, `supervise`, `closeout`, `remote-outcome` without `--fetch-billing`, `public-audit`, `validate-linear-issue`, `issue-intake`, `egress-plan`, `profiles`, `provider-capabilities`, and the `learnings` subcommands (`record`, `search`, `list`, `brief`, `promote`, `stats`). Read-only RunPod inspection commands such as `list-pods`, `get-pod`, `gpu-catalog`, and `runtime-metrics` require API access but do not create paid resources.

Read `references/runpod-operations.md` when a task involves enabling RunPod access, SSH/SCP, port exposure, monitoring, cost reporting, or worker coordination.

Read `references/cli-reference.md` when you need the full command inventory.

Read `references/worker-readiness.md` before assigning RunPod work to Symphony/Codex workers, especially when deciding whether a worker can mutate cloud resources or should produce a handoff packet only.

Read `references/failure-playbook.md` when pod create, boot, proxy, runtime metrics, git bootstrap, GPU scheduling, or artifact fetch behavior is ambiguous.

Read `references/provider-backed-workload-lessons.md` when adapting real project learnings into a RunPod manifest, especially for scientific, analytical, fanout, checkpointed, or multi-stage workloads.

Read `references/self-learning.md` when a provider step fails or surprises you: search the ledger first, record the issue/fix, launch a research sub-agent when stuck, and promote scrub-clean lessons into the provider entry.

In this repo, read `docs/private-source-storage-runbook.md` before changing source ingress, private image, RunPod S3, object-store, or network-volume strategy.

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
  artifact_hashes.jsonl
  closeout.json
  symphony_outcome.md
```

## Generic Use

Keep the bridge reusable for any Symphony + Linear setup. Domain workloads should pass contracts into this skill rather than becoming hard-coded behavior.

Before public release, run `cloud-bridge public-audit` and scrub organization-specific assumptions from examples and references.
