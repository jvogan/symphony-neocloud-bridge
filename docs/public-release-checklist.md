# Public Release Checklist

Run this checklist before publishing the repository or skill.

## Required Checks

```bash
bin/runpod-bridge public-audit
PYTHONPATH=src python3 -m unittest discover -s tests -v
bin/runpod-bridge provider-capabilities runpod
bin/runpod-bridge aws-orchestrator-plan examples/huge-sharded/launch_manifest.json
bin/runpod-bridge aws-orchestrator-plan examples/aws-orchestrated/launch_manifest.json
bin/runpod-bridge productivity-plan examples/proxy-matrix/launch_manifest.json
bin/runpod-bridge source-check examples/cheap-pod/launch_manifest.json
bin/runpod-bridge egress-plan examples/runpod-network-volume-s3/launch_manifest.json
bin/runpod-bridge render-runpodctl-create examples/cheap-pod/launch_manifest.json
bin/runpod-bridge profiles --recommend-for examples/huge-sharded/launch_manifest.json
bin/runpod-bridge contract-self-check examples/huge-sharded/launch_manifest.json
bin/runpod-bridge preflight examples/huge-sharded/launch_manifest.json
bin/runpod-bridge egress-plan examples/huge-sharded/launch_manifest.json
bin/runpod-bridge validate-linear-issue examples/proxy-matrix/linear_issue.md
bin/runpod-bridge issue-intake examples/proxy-matrix/linear_issue.md --manifest examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-intake
bin/runpod-bridge linear-comment LOCAL-RUNPOD-MATRIX --body-file templates/symphony-outcome.md
bin/runpod-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/runpod-bridge validate-manifest examples/public-smoke/launch_manifest.json
bin/runpod-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/runpod-bridge validate-manifest examples/proxy-matrix/launch_manifest.json
bin/runpod-bridge prepare examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-packet
bin/runpod-bridge validate-handoff .runtime/proxy-matrix-packet/provider_handoff.json || test $? -eq 1
bin/runpod-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
bin/runpod-bridge run-local examples/proxy-matrix/launch_manifest.json --repo-dir .runtime/proxy-matrix-repo --runtime-dir .runtime/proxy-matrix-run
bin/runpod-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
```

## Content Review

- No local absolute paths.
- No local-private install defaults in code, docs, examples, tests, wrappers, logs, or skill assets.
- No organization-specific demo names.
- No API keys, tokens, registry credentials, or private data.
- No presigned URLs; manifests must carry only runtime refs such as `archive_upload_url_ref`.
- No generated `runpod-execution/` packets.
- No tracked `.runtime`, cache, `__pycache__`, `.env*`, `env.sh`, closeout, handoff, or artifact-hash files.
- Examples remain `remote_launch_allowed: false`.
- Any remote smoke record is scrubbed before publishing.
- Any Linear issue example validates and contains no workspace-specific identifiers.
- HTTP proxy examples contain only sanitized artifacts and explain the public exposure risk.
- Top-level templates match the copies under `skills/runpod-symphony/assets/templates/`.
- `.codex/skills/runpod-symphony` resolves to the public skill source at `skills/runpod-symphony`.
- Skill references include generic worker-readiness and failure-playbook guidance, with no private run details.
- Self-learning notes are generalized into docs, tests, examples, or checks. Do not publish private scratch notes, pod IDs, raw logs, cost ledgers, or generated run packets.

## Operational Review

- Remote launch requires `launch_authorization`.
- Remote launch requires immutable source references.
- Remote launch requires a passing `contract-self-check`.
- Remote launch has finite budget and runtime limits.
- Mutating remote runs use the local launch lock, and shared orchestrator deployments set `RUNPOD_BRIDGE_LOCK_DIR`.
- Cleanup behavior is documented.
- `--max-spend-usd` is used for smoke tests.
- Cost closeout uses `cost-report` and billing API when available.
- Recovery playbooks use `recover-run` before manual console intervention.
- Neocloud hiccups feed the self-learning loop in `docs/neocloud-self-learning-runbook.md` before repeating the same paid route.
