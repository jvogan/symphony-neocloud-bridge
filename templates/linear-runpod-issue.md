## Summary

Run the declared workload on RunPod using the Symphony RunPod Bridge and return artifact-proof closeout.

## Inputs

- `launch_manifest` - path to validated RunPod launch manifest
- `repo_snapshot_or_commit` - exact source to execute
- `domain_contract` - workload-specific validation and expected artifacts

## Acceptance Criteria

- [ ] Local preflight passes before remote launch.
- [ ] Contract self-check passes and stage contract names real inputs, exact commands, route proof, expected outputs, done markers, resume policy, and claim level.
- [ ] `remote_launch_allowed: true` is present before any paid resource is created.
- [ ] Runtime and spend limits include `budget.max_runtime_minutes`, `budget.max_estimated_cost_usd`, and `budget.terminate_after_minutes` when using `runpodctl` creation.
- [ ] Pod ID, image, data center, runtime, and cost estimate are recorded.
- [ ] Pod state is polled and workload heartbeat/status files are captured.
- [ ] Declared validation commands pass.
- [ ] Expected artifacts exist and hashes are recorded.
- [ ] Pod is stopped or deleted unless retention is explicitly approved.
- [ ] If a network volume is attached, pod deletion and volume retention are documented.
- [ ] `symphony-outcome` block is completed.

## Validation Commands

```bash
runpod-bridge validate-manifest path/to/launch_manifest.json
runpod-bridge contract-self-check path/to/launch_manifest.json
runpod-bridge preflight path/to/launch_manifest.json
runpod-bridge egress-plan path/to/launch_manifest.json
runpod-bridge plan path/to/launch_manifest.json
runpod-bridge prepare path/to/launch_manifest.json --out-dir runpod-execution
runpod-bridge validate-handoff runpod-execution/provider_handoff.json
```

## Risk Notes

- Keep API keys, registry credentials, private datasets, and customer process records out of Linear and repo files. Use environment variables or runtime injection references.
- Artifact checks define success. Pod lifecycle events alone do not close a run.

<!-- symphony:schema
schema_version: 1
pack_id: symphony-runpod-bridge
pack_issue_id: runpod-smoke
touched_areas:
  - runpod-execution/
complexity: medium
-->
