# RunPod Bridge for Symphony + Linear

Local-first guardrails for AI agents running paid workloads on RunPod.

AI agents with cloud spend authority fail in three predictable ways: launching pods without explicit authorization, declaring success because the container exited cleanly, and leaking secrets into manifests, logs, or Linear comments. This bridge blocks all three. It turns a reviewed launch contract into a remote RunPod execution with preflight checks, artifact proof, cost records, and forced cleanup.

Built for OpenAI Symphony-style orchestration with Linear as the work ledger. Works with Codex workers, Claude Code workers, and mixed-agent Symphony lanes. The CLI is a stdlib-only Python tool that validates manifests, renders startup scripts, prepares handoff packets, runs local dry-runs, and guards paid pod creation.

![RunPod Bridge social preview](assets/social-preview/runpod-bridge-social-preview-01.png)

## What It Does

Three guardrails do most of the work:

- **Authorization gates.** Paid RunPod creation requires `remote_launch_allowed: true`, an explicit `launch_authorization` source, finite budget and runtime, an immutable repo reference, declared artifacts, validation commands, and a cleanup policy. Missing any of these and the agent stops at a dry-run plan.
- **Artifact proof for success.** Pod RUNNING, container exit codes, and log presence do not close a run as success. The run must produce declared artifacts at declared paths, with SHA-256 hashes that pass validation. Forbidden markers like `mock`, `fake`, `dummy`, or workload-specific placeholder names also fail closed.
- **Secret scrubbing.** Manifests, Linear bodies, repo files, and logs are screened for API keys, registry credentials, private datasets, and unpublished sequences. Secrets live in environment variables or runtime injection references.

Beyond the guardrails, the bridge renders auditable startup scripts and provider handoff packets, runs the same startup contract locally for dry-run validation, and monitors resource state plus workload heartbeats. It hashes declared artifacts, writes a parseable `symphony-outcome` closeout, and audits the public skill assets before publication. It also includes a neocloud self-learning runbook so provider hiccups become better checks, examples, and smoke ladders rather than repeated manual lessons.

## When To Use It

Use this bridge when an AI agent or orchestrator needs to run a declared batch workload on RunPod with a clear audit trail. It fits engineering jobs, model evaluation, dataset preprocessing, report generation, and other workloads that can declare commands, validation checks, and artifacts.

Skip it for: bypassing RunPod authorization, running long-lived public services, storing credentials in manifests, or claiming scientific or model-quality success without separate domain validation.

### Best fit

- RunPod users who want safer pod jobs with cost caps, cleanup proof, and artifact hashes.
- OpenAI Symphony-style multi-agent systems that dispatch Codex or Claude Code workers from Linear issues.
- Teams turning Linear tickets into remote batch workloads that need preflight checks and closeout records.
- AI agents that must prove what ran, where it ran, what it produced, what it cost, and whether the resource was cleaned up.
- Public demos that need local dry-runs without a RunPod API key.

### Agent prompts this handles

- "Validate this RunPod launch manifest before a paid run."
- "Turn this Linear issue into a provider handoff packet."
- "Run this workload on RunPod only if budget, artifact, validation, and cleanup gates pass."
- "Monitor a RunPod job and separate provider state from workload progress."
- "Close out a Symphony run with artifact hashes, cost records, and cleanup status."

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

For an agent-facing workflow, link `skills/runpod-symphony/` into the Codex or Claude Code lane environment, then run:

```bash
bin/runpod-bridge doctor
```

`doctor` warnings are acceptable for local dry-runs. Paid remote mutation also requires `RUNPOD_API_KEY`, a remote-ready manifest, and explicit execute flags.

## Safety Model

- Local dry-run is the default path.
- Paid RunPod creation requires `remote_launch_allowed: true`, explicit `launch_authorization`, finite budget and runtime, immutable source, expected artifacts, validation commands, and a cleanup policy.
- Remote create and cleanup commands require `--execute` plus explicit confirmation flags such as `--yes-create-paid-runpod` or `--yes-cleanup-runpod`.
- Nontrivial paid runs must expose a live productivity channel: sanitized `/healthz`, SSH or log tail, or another fetchable status packet.
- Success requires declared artifacts, validation checks, hashes, and cleanup status. Pod lifecycle events alone do not close a run.
- Secrets stay in secure stores or runtime injection. Manifests carry references, never literal values.
- Local dry-run runs without RunPod credentials.

## Flow

```text
Linear issue
  -> Symphony Codex or Claude Code worker
  -> local preflight
  -> RunPod launch or start
  -> startup workload
  -> logs, artifacts, hashes
  -> cleanup
  -> Linear symphony-outcome
```

RunPod runs the compute. Linear holds the audit trail. Symphony dispatches workers. The bridge turns an authorized issue plus a repo workload contract into a RunPod run with artifact proof and cleanup.

The bridge is agent-runtime agnostic. A Claude Code lane uses the same launch manifest and `runpod-bridge` CLI as a Codex lane. If a worker sandbox cannot reach RunPod directly, it can stop at `provider_handoff.json` and let a trusted orchestrator run `run-handoff`.

## Scope

The bridge is domain-agnostic. It works for:

- scientific and engineering batch jobs
- model evaluation and adapter jobs
- dataset preprocessing lanes
- figure, report, and artifact-generation lanes
- any Symphony and Linear workflow that can declare commands, validation checks, and artifacts

Domain repos define workload commands and success artifacts. The bridge validates and executes the remote compute contract.

## Local CLI

The CLI is stdlib-only Python. The full command inventory lives in `skills/runpod-symphony/references/cli-reference.md`. The most common local commands:

```bash
bin/runpod-bridge doctor
bin/runpod-bridge public-audit
bin/runpod-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/runpod-bridge contract-self-check examples/huge-sharded/launch_manifest.json
bin/runpod-bridge preflight examples/huge-sharded/launch_manifest.json
bin/runpod-bridge egress-plan examples/runpod-network-volume-s3/launch_manifest.json
bin/runpod-bridge profiles
bin/runpod-bridge plan examples/cheap-pod/launch_manifest.json
bin/runpod-bridge prepare examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-packet
bin/runpod-bridge render-startup examples/small-cpu/launch_manifest.json --out .runtime/startup.sh
bin/runpod-bridge run-local examples/cheap-pod/launch_manifest.json \
  --repo-dir .runtime/cheap-pod-repo \
  --runtime-dir .runtime/cheap-pod-run
```

After a run produces `runpod-execution/status.json` and heartbeats:

```bash
bin/runpod-bridge monitor examples/small-cpu/launch_manifest.json --base-dir .
bin/runpod-bridge supervise examples/small-cpu/launch_manifest.json --base-dir .
```

### Remote runs

Remote creation is guarded. `create-pod` writes an audited request and resource record without touching RunPod by default. Actual creation requires:

- `remote_launch_allowed: true` in the manifest
- explicit `launch_authorization`
- an immutable repo reference
- a passing `contract-self-check` with route proof
- `RUNPOD_API_KEY`
- no active duplicate pod prefix
- `--execute` and `--yes-create-paid-runpod`

`run-remote` and `run-handoff` also acquire an atomic local launch lock before pod creation. Set `RUNPOD_BRIDGE_LOCK_DIR` or `--lock-dir` if several orchestrators should share a lock directory.

For sandboxed Codex or Claude Code workers, prove the worker shell can reach RunPod REST before mutation. Some sandboxes have no outbound DNS or TCP even with `RUNPOD_API_KEY` injected. In that case, use the worker for `validate-manifest`, `prepare`, and `run-local`. The prepared packet includes `provider_handoff.json`; run that from an unsandboxed orchestrator or trusted `after_run` hook with `run-handoff`.

For a capped smoke, use the single-command remote runner. It creates the pod, verifies declared artifacts, and always attempts cleanup when a pod was created:

```bash
bin/runpod-bridge run-remote path/to/launch_manifest.json \
  --out-dir .runtime/remote-smoke \
  --max-spend-usd 5 \
  --verification-mode auto \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

The runner writes `.runtime/remote-smoke/remote_run_record.json` plus nested create, packet, and cleanup records. `--verification-mode auto` tries direct TCP artifact verification first, then the RunPod HTTP proxy fallback.

For worker-to-orchestrator handoff:

```bash
bin/runpod-bridge validate-handoff runpod-execution/provider_handoff.json
bin/runpod-bridge run-handoff runpod-execution/provider_handoff.json \
  --out-dir .runtime/handoff-run \
  --max-spend-usd 5 \
  --execute \
  --yes-create-paid-runpod \
  --yes-cleanup-runpod
```

Remote inspection and cleanup:

```bash
bin/runpod-bridge list-pods --name-prefix symphony-
bin/runpod-bridge get-pod POD_ID
bin/runpod-bridge runtime-metrics POD_ID --expected-elapsed-minutes 5 --json
bin/runpod-bridge pod-ssh-info POD_ID
bin/runpod-bridge cleanup-pod POD_ID --action delete
bin/runpod-bridge cost-report .runtime/remote-smoke/remote_run_record.json --fetch-billing
bin/runpod-bridge billing-pods --backend runpodctl --start-time 2026-05-01T00:00:00Z --bucket-size day
bin/runpod-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
```

For an orchestrator-side queue:

```bash
bin/runpod-bridge orchestrator-scan .runtime
bin/runpod-bridge orchestrator-once .runtime --out-root .runtime/orchestrator --max-spend-usd 5
```

Add `--execute --yes-create-paid-runpod --yes-cleanup-runpod` only after the handoff is validated and paid launch is authorized.

When a Linear closeout body is ready, post it with explicit mutation confirmation:

```bash
bin/runpod-bridge linear-comment TEAM-123 --body-file runpod-execution/symphony_outcome.md \
  --execute --yes-comment-linear
```

`preflight` reports rendered RunPod POST body size. Keep inline startup payloads below the bridge hard limit. Compress large embedded scripts and data, or move them to a repo, packet, network volume, or object store before remote launch.

HTTP proxy and direct TCP packet verification are inspection aids for sanitized, short-lived smoke artifacts. Production or private workloads use workspace archives plus SCP, network volume, presigned S3 upload, or object-store egress for durable artifact proof. `startup.progress.http_status_server_port` exposes a live `/healthz` progress endpoint during the workload. `startup.inspection.http_artifact_server_port` is a completion-only artifact server that starts after the workload reaches `inspection_hold`. `aws_s3_presigned_upload` uploads the archive with a runtime-injected S3 PUT URL and no AWS credentials in the pod. `object_store_upload` uses the AWS CLI when `RUNPOD_OBJECT_STORE_URI` and runtime credentials are injected.

## Symphony Ecosystem

This repo is the RunPod execution lane for teams adopting the public OpenAI Symphony pattern.

- [openai/symphony](https://github.com/openai/symphony): the upstream Symphony repo and service specification for Linear-driven autonomous implementation runs.
- [OpenAI Symphony article](https://openai.com/index/open-source-codex-orchestration-symphony/): background on using Linear as the control plane for coding agents.
- [jvogan/symphony-linear-starter](https://github.com/jvogan/symphony-linear-starter): public starter toolkit for Symphony and Linear operator workflows.
- [jvogan/symphony-claude-lane](https://github.com/jvogan/symphony-claude-lane): public companion lane for adding Claude Code workers to Symphony and Linear workflows.

The bridge stays useful outside that stack. The sharpest path runs Linear issue → Symphony Codex or Claude Code worker → guarded RunPod workload → artifact, cost, and cleanup proof → `symphony-outcome`.

## Starting Artifacts

- [skills/runpod-symphony/SKILL.md](skills/runpod-symphony/SKILL.md)
- [skills/runpod-symphony/references/worker-readiness.md](skills/runpod-symphony/references/worker-readiness.md)
- [skills/runpod-symphony/references/failure-playbook.md](skills/runpod-symphony/references/failure-playbook.md)
- [docs/product-brief.md](docs/product-brief.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/runpod-worker-readiness.md](docs/runpod-worker-readiness.md)
- [docs/runpod-observability-ladder.md](docs/runpod-observability-ladder.md)
- [docs/neocloud-self-learning-runbook.md](docs/neocloud-self-learning-runbook.md)
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

## Public Release

Run `bin/runpod-bridge public-audit` before publishing. It checks required release files, disallowed generated and private paths, repo-local skill linkage, template sync, source/docs/example text scans, JSON validity, manifest validity, contract self-checks, and Linear issue examples.

---

Independent project. Not affiliated with RunPod, Linear, or OpenAI.
