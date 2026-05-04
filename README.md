# Symphony RunPod Bridge

Reusable RunPod execution lane for Symphony + Linear.

## Thesis

RunPod should be a remote execution plane, not the orchestrator. Linear remains the scientific ledger. Symphony dispatches Codex workers. This bridge turns an authorized Linear issue plus a repo workload contract into a safe RunPod run with artifact proof and cleanup.

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

Run `bin/runpod-bridge public-audit` before publishing. It checks required release files, JSON validity, manifest validity, contract self-checks, and known local/internal text patterns.
