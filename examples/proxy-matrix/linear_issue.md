## Summary

Run the proxy-matrix smoke through the Symphony Cloud Bridge as a narrow Linear-dispatchable task. The task proves a multi-artifact workload can be validated locally, prepared for RunPod, inspected over HTTP proxy or TCP when authorized, and closed out from declared artifacts instead of pod lifecycle alone.

## Acceptance Criteria

- [ ] The proxy-matrix manifest validates with no errors.
- [ ] The rendered startup packet is produced and `startup.sh` passes shell syntax validation.
- [ ] The local dry-run succeeds and writes all declared matrix, shard, report, archive, status, heartbeat, and hash artifacts.
- [ ] The workload may emit only a draft closeout artifact; the bridge-written local closeout contains the authoritative `symphony-outcome` block and reports `artifact_execution_only`.
- [ ] Remote packet verification uses `verify-proxy-packet` or `verify-tcp-packet` only for sanitized smoke artifacts.
- [ ] Any remote launch remains blocked unless the issue explicitly authorizes paid resources, cleanup ownership, and a spend ceiling.

## Validation Commands

```bash
cloud-bridge validate-manifest examples/proxy-matrix/launch_manifest.json
cloud-bridge plan examples/proxy-matrix/launch_manifest.json
cloud-bridge prepare examples/proxy-matrix/launch_manifest.json --out-dir .runtime/proxy-matrix-packet
bash -n .runtime/proxy-matrix-packet/startup.sh
cloud-bridge run-local examples/proxy-matrix/launch_manifest.json --repo-dir .runtime/proxy-matrix-local-repo --runtime-dir .runtime/proxy-matrix-local-run
cloud-bridge validate-linear-issue examples/proxy-matrix/linear_issue.md
```

## Touched Areas

- `examples/proxy-matrix/` - manifest and Linear issue example for the matrix smoke.
- `runpod-execution/` - runtime packet shape produced by the startup contract.
- `.runtime/proxy-matrix-*` - disposable local validation output.

## Routing

lane: codex
kind: validation
visual_qa_required: false

## Dependencies

Blocked by: none

## Risk Notes

- HTTP proxy artifact inspection exposes a public temporary service; use only sanitized smoke artifacts on that path.
- Remote creation still needs explicit launch authorization, `--max-spend-usd`, and cleanup confirmation.
- A pod that starts successfully is not enough evidence; declared artifacts and hashes define success.

## Complexity

tier: medium

<!-- symphony:schema
schema_version: 1
pack_id: symphony-cloud-bridge
pack_issue_id: proxy-matrix-smoke
lane: codex
kind: validation
visual_qa_required: false
touched_areas:
  - examples/proxy-matrix/
  - runpod-execution/
  - .runtime/proxy-matrix-*
complexity: medium
-->
