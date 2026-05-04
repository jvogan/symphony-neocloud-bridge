# RunPod Bridge for Symphony + Linear

Local-first guardrails for AI agents running declared workloads on RunPod.

This project helps AI-agent workflows turn a reviewed workload contract into a remote RunPod execution with preflight checks, artifact proof, cost records, and cleanup. It is designed for OpenAI Symphony-style orchestration with Linear as the work ledger, but the core CLI is just a standard-library Python tool that can validate manifests, render startup scripts, prepare handoff packets, run local dry-runs, and guard paid RunPod pod creation.

![RunPod Bridge social preview](assets/social-preview/runpod-bridge-social-preview-01.png)

It is not a domain-science framework and it is not a general RunPod SDK. Domain repos provide commands, validation checks, and expected artifacts; this bridge owns the remote execution mechanics and blocks false success. This is an independent public bridge and is not an official RunPod, Linear, or OpenAI project.

## What It Does

- Validates launch manifests before any paid resource can be created.
- Renders auditable startup scripts and provider handoff packets.
- Runs the same startup contract locally for dry-run validation.
- Guards RunPod pod creation behind authorization, budget, immutable source, artifact, and cleanup gates.
- Monitors resource state, runtime health, workload heartbeats, logs, and live productivity channels.
- Hashes declared artifacts and writes a parseable `symphony-outcome` closeout.
- Provides public-release audits so examples, skill assets, and docs stay scrubbed.

## When To Use It

Use this bridge when an AI agent or orchestrator needs to run a declared batch workload on RunPod and you need a clear audit trail. It is useful for engineering jobs, model evaluation, dataset preprocessing, report generation, and other workloads that can define commands, validation checks, and artifacts.

Do not use it to bypass RunPod authorization, run long-lived public services, store credentials in manifests, or claim scientific/model success without separate domain validation.

## Best Fit

- RunPod users who want safer pod-based GPU or CPU jobs with cost caps, cleanup proof, and artifact hashes.
- OpenAI Symphony-style multi-agent systems that dispatch Codex workers from Linear issues.
- Teams turning Linear tickets into remote batch workloads that need preflight checks and closeout records.
- AI agents that must prove what ran, where it ran, what it produced, what it cost, and whether the resource was cleaned up.
- Public demos that need local dry-runs without requiring a RunPod API key.

## Symphony Ecosystem

This repo is intended to be the RunPod execution lane for teams adopting the public OpenAI Symphony pattern:

- [openai/symphony](https://github.com/openai/symphony): the upstream Symphony repo and service specification for Linear-driven autonomous implementation runs.
- [OpenAI Symphony article](https://openai.com/index/open-source-codex-orchestration-symphony/): background on using Linear as the control plane for coding agents.
- [jvogan/symphony-linear-starter](https://github.com/jvogan/symphony-linear-starter): public starter toolkit for Symphony + Linear operator workflows.
- [jvogan/symphony-claude-lane](https://github.com/jvogan/symphony-claude-lane): public companion lane for adding Claude Code to Symphony + Linear workflows.

The bridge stays useful outside that stack, but its sharpest path is: Linear issue -> Symphony worker -> guarded RunPod workload -> artifact/cost/cleanup proof -> `symphony-outcome`.

## Agent Prompts This Handles

- "Validate this RunPod launch manifest before a paid run."
- "Turn this Linear issue into a provider handoff packet."
- "Run this workload on RunPod only if budget, artifact, validation, and cleanup gates pass."
- "Monitor a RunPod job and separate provider state from workload progress."
- "Close out a Symphony run with artifact hashes, cost records, and cleanup status."

## Flow

```text
Linear issue
  -> Symphony Codex worker
  -> local preflight
  -> RunPod launch or start
  -> startup workload
  -> logs/artifacts/hashes
  -> cleanup
  -> Linear symphony-outcome
```

RunPod is treated as the remote execution plane, not the orchestrator. Linear remains the work ledger. Symphony dispatches workers. The bridge turns an authorized issue plus a repo workload contract into a RunPod run with artifact proof and cleanup.

## Quick Start

No RunPod API key is needed for local validation:

```bash
python3 -m pip install -e .
bin/runpod-bridge public-audit
bin/runpod-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/runpod-bridge plan examples/cheap-pod/launch_manifest.json
bin/runpod-bridge prepare examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-packet
bin/runpod-bridge run-local examples/cheap-pod/launch_manifest.json \
  --repo-dir .runtime/cheap-pod-repo \
  --runtime-dir .runtime/cheap-pod-run
```

For an agent-facing workflow, install or link the skill at `skills/runpod-symphony/` into the relevant Codex skill home, then run:

```bash
bin/runpod-bridge doctor
```

`doctor` warnings are acceptable for local dry-runs. Paid remote mutation additionally requires `RUNPOD_API_KEY`, a remote-ready manifest, and explicit execute flags.

## Safety Model

- Local dry-run is the default path.
- Paid RunPod mutation requires `remote_launch_allowed: true`, explicit `launch_authorization`, finite budget/runtime, immutable source, expected artifacts, validation commands, and cleanup policy.
- Remote create/cleanup commands require `--execute` plus explicit confirmation flags.
- Nontrivial paid runs must expose a live productivity channel such as sanitized `/healthz`, SSH/log tail, or another fetchable status packet.
- Success requires declared artifacts, validation checks, hashes, and cleanup status. Pod lifecycle events alone are not success.

## Scope

This bridge is domain-agnostic. It should work for:

- scientific and engineering batch jobs
- model evaluation or adapter jobs
- dataset preprocessing lanes
- figure, report, and artifact-generation lanes
- any Symphony + Linear workflow that can declare commands, validation checks, and artifacts

Domain repos define workload commands and success artifacts. This bridge validates and executes the remote compute contract.

## Non-Negotiables

- No false success: remote run success requires declared artifacts and checks, not just pod lifecycle events.
- Remote launch is opt-in and must be explicitly authorized.
- Secrets stay in secure stores or runtime injection, never templates or repo files.
- Cleanup status is part of the outcome.
- Local dry-run must be possible without RunPod.

## Starting Artifacts

- [skills/runpod-symphony/SKILL.md](skills/runpod-symphony/SKILL.md)
- [skills/runpod-symphony/references/worker-readiness.md](skills/runpod-symphony/references/worker-readiness.md)
- [skills/runpod-symphony/references/failure-playbook.md](skills/runpod-symphony/references/failure-playbook.md)
- [docs/product-brief.md](docs/product-brief.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/runpod-worker-readiness.md](docs/runpod-worker-readiness.md)
- [docs/runpod-observability-ladder.md](docs/runpod-observability-ladder.md)
- [docs/provider-adapter-contract.md](docs/provider-adapter-contract.md)
- [docs/runpod-official-surfaces.md](docs/runpod-official-surfaces.md)
- [docs/runpod-superpowers-2026-05.md](docs/runpod-superpowers-2026-05.md)
- [docs/aws-runpod-superpowers.md](docs/aws-runpod-superpowers.md)
- [docs/discovery.md](docs/discovery.md)
- [templates/runpod-launch-manifest.template.json](templates/runpod-launch-manifest.template.json)
- [templates/linear-runpod-issue.md](templates/linear-runpod-issue.md)
- [templates/symphony-outcome.md](templates/symphony-outcome.md)
- [docs/public-release-checklist.md](docs/public-release-checklist.md)
- [docs/remote-smoke-runbook.md](docs/remote-smoke-runbook.md)

## Local CLI

The bridge has a local-first stdlib Python CLI. Run it from the repo with:

```bash
bin/runpod-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/runpod-bridge contract-self-check examples/huge-sharded/launch_manifest.json
bin/runpod-bridge doctor
bin/runpod-bridge public-audit
bin/runpod-bridge provider-capabilities runpod
bin/runpod-bridge aws-orchestrator-plan examples/huge-sharded/launch_manifest.json
bin/runpod-bridge aws-orchestrator-plan examples/aws-orchestrated/launch_manifest.json
bin/runpod-bridge productivity-plan examples/proxy-matrix/launch_manifest.json
bin/runpod-bridge source-check examples/cheap-pod/launch_manifest.json
bin/runpod-bridge egress-plan examples/runpod-network-volume-s3/launch_manifest.json
bin/runpod-bridge profiles
bin/runpod-bridge validate-linear-issue examples/proxy-matrix/linear_issue.md
bin/runpod-bridge linear-issue TEAM-123 --out .runtime/TEAM-123.md
bin/runpod-bridge issue-intake examples/proxy-matrix/linear_issue.md --manifest examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-intake
bin/runpod-bridge preflight examples/huge-sharded/launch_manifest.json
bin/runpod-bridge egress-plan examples/huge-sharded/launch_manifest.json
bin/runpod-bridge plan examples/public-smoke/launch_manifest.json
bin/runpod-bridge plan examples/cheap-pod/launch_manifest.json
bin/runpod-bridge plan examples/proxy-matrix/launch_manifest.json
bin/runpod-bridge prepare examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-packet
bin/runpod-bridge render-runpodctl-create examples/cheap-pod/launch_manifest.json
bin/runpod-bridge prepare examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-packet
bin/runpod-bridge validate-handoff .runtime/proxy-matrix-packet/provider_handoff.json || test $? -eq 1
bin/runpod-bridge plan examples/small-cpu/launch_manifest.json
bin/runpod-bridge render-startup examples/small-cpu/launch_manifest.json --out .runtime/startup.sh
bin/runpod-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
bin/runpod-bridge run-local examples/proxy-matrix/launch_manifest.json --repo-dir .runtime/proxy-matrix-repo --runtime-dir .runtime/proxy-matrix-run
bin/runpod-bridge create-pod examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-remote || test $? -eq 2
```

After a run has produced `runpod-execution/status.json` and heartbeats, inspect it with:

```bash
bin/runpod-bridge monitor examples/small-cpu/launch_manifest.json --base-dir .
bin/runpod-bridge supervise examples/small-cpu/launch_manifest.json --base-dir .
```

Remote creation is guarded. `create-pod` writes an audited request/resource record without touching RunPod by default, and actual creation requires `remote_launch_allowed: true`, explicit `launch_authorization`, an immutable repo reference, a passing `contract-self-check` with route proof, `RUNPOD_API_KEY`, no active duplicate pod prefix, `--execute`, and `--yes-create-paid-runpod`. The mutating `run-remote` and `run-handoff` flows also take an atomic local launch lock before pod creation; set `RUNPOD_BRIDGE_LOCK_DIR` or `--lock-dir` if several orchestrators should share one lock directory.

For Symphony/Codex workers, first prove the worker shell can reach RunPod REST. Some sandboxed worker runtimes have no outbound DNS/TCP even when `RUNPOD_API_KEY` is injected. In that mode, use the worker for `validate-manifest`, `prepare`, and `run-local`. The prepared packet includes `provider_handoff.json`; run that from an unsandboxed orchestrator or trusted `after_run` hook with `run-handoff`.

For a capped smoke, prefer the single-command remote runner. It creates the pod, verifies declared artifacts, and always attempts cleanup when a pod was created:

```bash
bin/runpod-bridge run-remote path/to/launch_manifest.json \
  --out-dir .runtime/remote-smoke \
  --max-spend-usd 5 \
  --verification-mode auto \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

The runner writes one top-level `.runtime/remote-smoke/remote_run_record.json` plus nested create, packet, and cleanup records. `--verification-mode auto` tries direct TCP artifact verification first, then the RunPod HTTP proxy fallback.

For worker-to-orchestrator handoff, use the provider handoff instead of retyping the manifest path:

```bash
bin/runpod-bridge validate-handoff runpod-execution/provider_handoff.json
bin/runpod-bridge run-handoff runpod-execution/provider_handoff.json \
  --out-dir .runtime/handoff-run \
  --max-spend-usd 5 \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

Remote inspection and cleanup commands are also available:

```bash
bin/runpod-bridge list-pods --name-prefix symphony-
bin/runpod-bridge get-pod POD_ID
bin/runpod-bridge runtime-metrics POD_ID --expected-elapsed-minutes 5 --json
bin/runpod-bridge pod-ssh-info POD_ID
bin/runpod-bridge verify-proxy-packet examples/proxy-matrix/launch_manifest.json POD_ID --port 8000 --out-dir .runtime/proxy-matrix-proxy
bin/runpod-bridge verify-tcp-packet examples/proxy-matrix/launch_manifest.json POD_ID --port 8000 --out-dir .runtime/proxy-matrix-tcp
bin/runpod-bridge cleanup-pod POD_ID --action delete
bin/runpod-bridge cost-report .runtime/remote-smoke/remote_run_record.json --fetch-billing
bin/runpod-bridge billing-endpoints --start-time 2026-05-01T00:00:00Z --bucket-size day
bin/runpod-bridge billing-network-volumes --start-time 2026-05-01T00:00:00Z --bucket-size day
bin/runpod-bridge billing-pods --backend runpodctl --start-time 2026-05-01T00:00:00Z --bucket-size day
bin/runpod-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
```

`preflight` reports rendered RunPod POST body size. Keep inline startup payloads below the bridge hard limit; large embedded scripts/data should be compressed or moved to a repo, packet, network volume, or object store before remote launch.

HTTP proxy and direct TCP packet verification are inspection aids for sanitized, short-lived smoke artifacts. Production or private workloads should use workspace archives plus SCP, network volume, presigned S3 upload, or object-store egress for durable artifact proof. `startup.progress.http_status_server_port` can expose a live `/healthz` progress endpoint during the workload; `startup.inspection.http_artifact_server_port` is a completion-only artifact server that starts after the workload reaches `inspection_hold`. `aws_s3_presigned_upload` can upload the archive with a runtime-injected S3 PUT URL and no AWS credentials in the pod. `object_store_upload` startup support can upload the archive and hash file with the AWS CLI when `RUNPOD_OBJECT_STORE_URI` and runtime credentials are injected.

For an orchestrator-side queue, scan or run prepared handoffs:

```bash
bin/runpod-bridge orchestrator-scan .runtime
bin/runpod-bridge orchestrator-once .runtime \
  --out-root .runtime/orchestrator \
  --max-spend-usd 5
```

Use `--execute --yes-create-paid-runpod --yes-cleanup-runpod` only after the handoff is validated and paid launch is authorized.

When a Linear closeout body is ready, post it only with explicit mutation confirmation:

```bash
bin/runpod-bridge linear-comment TEAM-123 --body-file runpod-execution/symphony_outcome.md --execute --yes-comment-linear
```

## Public Release

Run `bin/runpod-bridge public-audit` before publishing. It checks required release files, disallowed generated/private paths, repo-local skill linkage, template sync, source/docs/example text scans, JSON validity, manifest validity, contract self-checks, and Linear issue examples.
