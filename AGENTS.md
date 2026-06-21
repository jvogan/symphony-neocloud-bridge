# Symphony Cloud Bridge Agent Guide

This repo is the planning and implementation workspace for a reusable RunPod execution lane for Symphony + Linear.

## Mission

Build a domain-agnostic sidecar that lets Symphony workers run repo-defined workloads on RunPod safely and repeatably.

The bridge should own remote execution mechanics:

- compute policy selection
- launch manifest validation
- local preflight before paid resources
- RunPod pod/template/volume lifecycle
- startup command generation
- log/artifact capture
- artifact hash reporting
- cost and billing closeout
- recovery and dashboard records
- cleanup enforcement
- Linear `symphony-outcome` closeout blocks

It should not own domain science. Domain agents should pass workload contracts into this bridge.

## Operating Rules

- Default to local dry-run validation until the operator explicitly authorizes remote launch.
- Do not create paid RunPod resources unless the issue/manifest has `remote_launch_allowed: true`, budget/time limits, cleanup policy, and expected artifacts.
- Default paid Pods to RunPod Secure Cloud. Use Community Cloud only for explicit public/synthetic sanitized smokes with `safety.community_cloud_allowed: true`; never use it for private source, secrets, durable volumes, unpublished data, or production claims.
- Never store API keys, tokens, registry credentials, private datasets, unpublished sequences, or raw customer process records in this repo or Linear.
- Treat pod creation, pod start, command exit, and log presence as insufficient for success. Success requires declared artifact checks.
- Require `contract-self-check` before paid launch so real inputs, exact commands, route proof, expected outputs, done markers, resume policy, and claim level are explicit.
- Stop or delete pods at closeout unless retention is explicitly approved and documented.
- Keep provider-specific logic isolated so provider-neutral cloud/neocloud adapters can reuse the same contract.

## Key Pattern And Convention Notes

- Use `skills/cloud-symphony/` as the Codex skill source of truth.
- Use `bin/cloud-bridge` as the stable local CLI wrapper. Symphony workers can use any equivalent wrapper on their `PATH`.
- Keep reusable manifests under `templates/` and concrete smoke examples under `examples/`.
- Keep the provider-neutral contract in `provider`, `workload`, `startup`, `monitoring`, `artifact_egress`, `worker_coordination`, and `closeout`.
- Put RunPod-specific resource fields only under the `runpod` block so other neocloud adapters can reuse the common contract later.
- For huge tasks, require checkpoint policy, explicit artifact egress, silence timeout, and cleanup ownership.
- For cheap/small tasks, prefer CPU-only, no ports, no network volume, short runtime, and a tiny artifact self-check.

## Risk Areas

- Remote launch is high risk because it can create paid resources. Keep `create-pod` blocked unless launch authorization, budget, immutable source, and explicit execute flags are present.
- Secret handling is high risk. Do not put literal tokens or credentials in manifests, Linear issues, logs, or examples.
- Cleanup is high risk. A run is not complete unless stop/delete or approved retention is recorded.
- Artifact proof is high risk. Do not close as success from pod lifecycle, command submission, or logs alone.
- Cross-repo discoverability is fragile. Keep normal Codex, Symphony worker `CODEX_HOME`, and repo-local `AGENTS.md` references aligned.

## Preferred Validation Commands

Validate docs, templates, examples, and the local CLI:

```bash
find docs logs templates skills examples tests -maxdepth 3 -type f | sort
python3 -m json.tool templates/runpod-launch-manifest.template.json >/dev/null
PYTHONPATH=src python3 -m unittest discover -s tests -v
bin/cloud-bridge doctor || test $? -eq 2
bin/cloud-bridge audit-manifests .
bin/cloud-bridge public-audit
bin/cloud-bridge provider-capabilities runpod
bin/cloud-bridge billing-endpoints || test $? -eq 1
bin/cloud-bridge billing-network-volumes || test $? -eq 1
bin/cloud-bridge render-runpodctl-create examples/cheap-pod/launch_manifest.json
bin/cloud-bridge pod-ssh-info dummy-pod-id || test $? -eq 1
bin/cloud-bridge profiles --recommend-for examples/huge-sharded/launch_manifest.json
bin/cloud-bridge contract-self-check examples/huge-sharded/launch_manifest.json
bin/cloud-bridge preflight examples/huge-sharded/launch_manifest.json
bin/cloud-bridge egress-plan examples/huge-sharded/launch_manifest.json
bin/cloud-bridge validate-manifest examples/public-smoke/launch_manifest.json
bin/cloud-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/cloud-bridge prepare examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-packet
bin/cloud-bridge validate-handoff .runtime/cheap-pod-packet/provider_handoff.json || test $? -eq 1
bin/cloud-bridge validate-manifest examples/small-cpu/launch_manifest.json
bin/cloud-bridge validate-manifest examples/huge-sharded/launch_manifest.json
bin/cloud-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
bin/cloud-bridge issue-intake examples/proxy-matrix/linear_issue.md --manifest examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-intake
bin/cloud-bridge linear-comment LOCAL-RUNPOD-MATRIX --body-file templates/symphony-outcome.md || test $? -eq 0
bin/cloud-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
bin/cloud-bridge create-pod examples/cheap-pod/launch_manifest.json --out-dir .runtime/cheap-pod-remote || test $? -eq 2
bin/cloud-bridge run-remote examples/public-smoke/launch_manifest.json --out-dir .runtime/public-smoke-remote || test $? -eq 2
```

## Initial Build Direction

Start with a local stdlib Python package and CLI:

- `cloud-bridge validate-manifest`
- `cloud-bridge render-startup`
- `cloud-bridge plan`
- `cloud-bridge write-handoff`, `validate-handoff`, and `run-handoff`
- `cloud-bridge contract-self-check`, `preflight`, `egress-plan`, `profiles`, and `provider-capabilities`
- `cloud-bridge issue-intake`, `orchestrator-scan`, `orchestrator-once`, `supervise`, `dashboard`, `cost-report`, and `recover-run`
- `cloud-bridge billing-pods`, `billing-endpoints`, and `billing-network-volumes`
- `cloud-bridge runtime-metrics` for read-only GraphQL container uptime/utilization probes and crash-loop detection
- `cloud-bridge render-runpodctl-create` and `pod-ssh-info` when `runpodctl` is installed
- `cloud-bridge create-pod` only after explicit launch policy passes
- `cloud-bridge closeout`

First integrations should use cheap CPU smoke manifests before GPU or long-running workloads.
