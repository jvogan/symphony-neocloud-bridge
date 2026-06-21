# Public Release Checklist

Run this checklist before publishing the repository or skill.

## Required Checks

```bash
bin/cloud-bridge public-audit
bin/cloud-bridge audit-manifests .
PYTHONPATH=src python3 -m unittest discover -s tests -v
bin/cloud-bridge provider-capabilities runpod
bin/cloud-bridge aws-orchestrator-plan examples/huge-sharded/launch_manifest.json
bin/cloud-bridge aws-orchestrator-plan examples/aws-orchestrated/launch_manifest.json
bin/cloud-bridge productivity-plan examples/proxy-matrix/launch_manifest.json
bin/cloud-bridge source-check examples/cheap-pod/launch_manifest.json
bin/cloud-bridge egress-plan examples/runpod-network-volume-s3/launch_manifest.json
bin/cloud-bridge render-runpodctl-create examples/cheap-pod/launch_manifest.json
bin/cloud-bridge profiles --recommend-for examples/huge-sharded/launch_manifest.json
bin/cloud-bridge contract-self-check examples/huge-sharded/launch_manifest.json
bin/cloud-bridge preflight examples/huge-sharded/launch_manifest.json
bin/cloud-bridge egress-plan examples/huge-sharded/launch_manifest.json
bin/cloud-bridge validate-linear-issue examples/proxy-matrix/linear_issue.md
bin/cloud-bridge issue-intake examples/proxy-matrix/linear_issue.md --manifest examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-intake
bin/cloud-bridge linear-comment LOCAL-RUNPOD-MATRIX --body-file templates/symphony-outcome.md
bin/cloud-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/cloud-bridge validate-manifest examples/public-smoke/launch_manifest.json
bin/cloud-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/cloud-bridge validate-manifest examples/proxy-matrix/launch_manifest.json
bin/cloud-bridge prepare examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-packet
bin/cloud-bridge validate-handoff .runtime/proxy-matrix-packet/provider_handoff.json || test $? -eq 1
bin/cloud-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
bin/cloud-bridge run-local examples/proxy-matrix/launch_manifest.json --repo-dir .runtime/proxy-matrix-repo --runtime-dir .runtime/proxy-matrix-run
bin/cloud-bridge dashboard --scan-dir .runtime --out .runtime/runpod-dashboard.html
```

## Content Review

- No local absolute paths.
- No organization-specific demo names.
- No API keys, bearer tokens, AWS secrets, registry credentials, private keys, connection codes, or private data.
- No literal high-confidence secret strings.
- No presigned URLs; manifests must carry only runtime refs such as `archive_upload_url_ref`.
- `public-audit` scans docs, templates, examples, skill files, source, and tests for public-readiness leaks.
- No generated `runpod-execution/` packets.
- Examples remain `remote_launch_allowed: false`.
- Examples and docs do not tell operators to create public repos/images for private work; public GitHub/GHCR/Docker Hub is allowed only for sanitized smokes.
- Any remote smoke record is scrubbed before publishing.
- Any Linear issue example validates and contains no workspace-specific identifiers.
- HTTP proxy examples contain only sanitized artifacts and explain the public exposure risk.

## Operational Review

- Remote launch requires `launch_authorization`.
- Remote launch requires immutable source references.
- Remote launch requires a passing `contract-self-check`.
- Remote launch has finite budget and runtime limits.
- Mutating remote runs use the local launch lock, and shared orchestrator deployments set `RUNPOD_BRIDGE_LOCK_DIR`.
- Provider `RUNNING`, pod creation, command exit, and log presence are readiness signals only. Success requires declared artifact checks, validation, hashes, and cleanup or approved retention.
- Cleanup behavior is documented.
- `--max-spend-usd` is used for smoke tests.
- Cost closeout uses `cost-report` and billing API when available.
- Recovery playbooks use `recover-run` before manual console intervention.
