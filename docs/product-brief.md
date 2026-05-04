# Product Brief

## Name

RunPod Bridge for Symphony + Linear

## Purpose

Provide a reusable, contract-driven RunPod lane for OpenAI Symphony-style agent orchestration and Linear issue workflows.

## Audience

- RunPod users who want guarded remote jobs rather than ad hoc pod starts.
- OpenAI Symphony-style orchestrators that dispatch Codex, Claude Code, or mixed-agent workers from Linear issues.
- AI agents that need manifests, dry-runs, provider handoffs, artifact proof, cleanup, and parseable closeout records.

## User Story

An operator has a Linear issue that says a workload should run on RunPod. A Symphony Codex or Claude Code worker validates the local contract, starts or creates the pod only when authorized, runs the startup workload, captures proof artifacts, stops/deletes resources, and writes a parseable outcome block.

## Product Unit

A remote execution packet:

```text
runpod-execution/
  launch_manifest.json
  provider_handoff.json
  startup.sh
  local_preflight.json
  monitor_events.ndjson
  status.json
  egress_status.json
  runpod_resource_record.json
  logs/
  artifacts/
  artifact_hashes.json
  closeout.json
  symphony_outcome.md
```

## Success Criteria

- Manifest validation catches missing authorization, budget, cleanup, expected artifacts, validation commands, monitoring contract, artifact egress, and secret-like literal env vars.
- Local dry-run can render a launch packet, provider handoff, startup scripts, and expected closeout without touching RunPod.
- Authorized pod smoke can create or start a RunPod pod through an audited request, record pod ID, image, region/data center, runtime, cost estimate, and cleanup status.
- Symphony workers can poll pod state without owning mutation rights, and only one worker can own launch/cleanup for a run.
- Trusted orchestrators can consume `provider_handoff.json` packets, apply launch locks, query billing, render dashboards, and run recovery cleanup.
- Large workloads have explicit profile, checkpoint, durable egress, and supervisor recommendations before paid launch.
- Success is blocked unless declared artifact checks pass.

## Out Of Scope For V1

- Autonomous private data sync
- Long-lived production service orchestration
- Kubernetes
- Full non-RunPod provider adapters
- GxP/GMP package generation
- Domain science or model interpretation
