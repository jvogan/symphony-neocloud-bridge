## Summary

Run the declared workload on RunPod using the Symphony Cloud Bridge and return artifact-proof closeout.

## Inputs

- `launch_manifest` - concrete workload manifest selected for the run, such as `examples/public-smoke/launch_manifest.json`.
- `source_ref` - immutable source commit, prepared snapshot, or inline command packet to execute.
- `domain_contract` - workload-specific validation, done markers, and expected artifacts.
- `launch_authorization` - explicit approval source, spend ceiling, runtime ceiling, and cleanup owner.

## Acceptance Criteria

- [ ] Local preflight passes before remote launch.
- [ ] Contract self-check passes and stage contract names real inputs, exact commands, route proof, expected outputs, done markers, resume policy, and claim level.
- [ ] `remote_launch_allowed: true` is present before any paid resource is created.
- [ ] Runtime and spend limits include `budget.max_runtime_minutes`, `budget.max_estimated_cost_usd`, and `budget.terminate_after_minutes` when using `runpodctl` creation.
- [ ] Billing attribution is declared with `billing.cost_center`, `billing.project_code`, or documented reason for leaving it uncategorized.
- [ ] If `runpod.interruptible: true`, checkpoint/resume policy and durable egress are explicit before launch.
- [ ] Pod ID, image, data center, runtime, and cost estimate are recorded.
- [ ] Every monitor update reports `progress-report classification.state` verbatim, plus `workload_progressing`, `monitor_alive`, `outage_suspected`, and `next_action`; do not paraphrase these as healthy, green, stalled, or outage.
- [ ] Workload progress is claimed only when `classification.workload_progressing: true` or terminal artifacts validate.
- [ ] Declared validation commands pass.
- [ ] Expected artifacts exist and hashes are recorded.
- [ ] Pod cleanup is verified as stopped/deleted or `already_absent`; `cleanup.status: submitted` alone is not a completed run.
- [ ] If a network volume is attached, pod deletion proof and volume retention are documented.
- [ ] `symphony-outcome` block is completed.

## Validation Commands

```bash
bin/cloud-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/cloud-bridge contract-self-check templates/runpod-launch-manifest.template.json
bin/cloud-bridge preflight templates/runpod-launch-manifest.template.json
bin/cloud-bridge egress-plan templates/runpod-launch-manifest.template.json
bin/cloud-bridge plan templates/runpod-launch-manifest.template.json
bin/cloud-bridge prepare templates/runpod-launch-manifest.template.json --out-dir .runtime/runpod-template-handoff
bin/cloud-bridge validate-handoff .runtime/runpod-template-handoff/provider_handoff.json || test $? -eq 1
```

## Touched Areas

- `templates/runpod-launch-manifest.template.json` - starting manifest shape for the workload.
- `runpod-execution/` - provider handoff packet produced for the run.
- `.runtime/runpod-template-handoff/` - disposable local validation output.

## Routing

lane: codex
kind: runpod-execution
visual_qa_required: false

## Risk Notes

- Do not put API keys, registry credentials, private datasets, or customer process records in Linear or repo files.
- Pod lifecycle events are not success. Artifact checks define success.

## Complexity

tier: medium

<!-- symphony:schema
schema_version: 1
pack_id: symphony-cloud-bridge
pack_issue_id: runpod-smoke
lane: codex
kind: runpod-execution
visual_qa_required: false
touched_areas:
  - templates/runpod-launch-manifest.template.json
  - runpod-execution/
  - .runtime/runpod-template-handoff/
complexity: medium
-->
