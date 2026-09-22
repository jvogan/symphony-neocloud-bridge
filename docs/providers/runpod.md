# RunPod provider reference

Lifecycle and API compatibility sources reviewed: 2026-09-22.

## Bridge support

RunPod Pods have guarded automated support for create, observe, artifact verification, billing evidence, and cleanup. The bridge also provides read-only or planning commands for GPU catalog, runtime metrics, SSH information, network-volume egress, and provider billing.

The implemented pod transport is `runpod_pod_v1`, which still calls legacy REST API v1. RunPod made REST API v2 generally available on August 18, 2026, deprecated v1, and documents v1 retirement on November 15, 2026. Treat the current runner as a compatibility path that must migrate before that date.

## Launch contract

A paid Pod launch requires:

- explicit remote-launch authorization;
- a finite runtime and cost budget;
- reproducible source and exact startup commands;
- declared validation commands and required artifacts;
- an artifact-egress path;
- one mutating worker and a local launch lock;
- explicit create and cleanup confirmations.

Provider state is separate from workload state. A Pod can be running while startup is failing or the workload is idle. Closeout therefore requires retrieved artifacts, validation, hashes, cost evidence, and verified deletion or approved retention.

## Current control planes

| Surface | Bridge status | Notes |
| --- | --- | --- |
| REST API v1 Pods and billing | Implemented, legacy | Must migrate before v1 retirement. |
| GraphQL GPU catalog and runtime samples | Implemented, read-only | Useful for placement and diagnostics; GraphQL is also on a retirement path. |
| REST API v2 | Planned migration | Not yet used by the bridge runner. |
| `runpodctl` | Optional | Used for rendered command review, SSH information, and billing fallback. |
| Serverless, Flash, and Instant Clusters | Documented only | No guarded bridge runner yet. |
| Official RunPod plugin and MCP server | Optional external tools | Their availability does not bypass bridge policy gates. |

RunPod's v2 migration is more than a base-URL change. Request envelopes, list responses, lifecycle actions, errors, billing routes, and catalog access differ. The migration should update the transport and its fixtures together, then exercise the same artifact and cleanup gates before `runpod_pod_v2` is marked automated.

## `runpodctl` compatibility

`runpodctl` v2.10.0 added Pod and Serverless log streaming. Version 2.12.0 removed `pod create --stop-after` and `--terminate-after`. These are historical compatibility changes; inspect the installed CLI before generating commands.

The bridge therefore treats `budget.terminate_after_minutes` as an orchestrator cleanup deadline. It does not render either removed lifecycle flag and does not claim that the provider enforces this field. Provider documentation may lag the released CLI, so scripts should pin or inspect the installed CLI version instead of assuming flags exist.

## Monitoring and evidence

- REST lifecycle fields show provider intent and allocation state.
- GraphQL runtime samples can reveal uptime resets, resource use, and possible crash loops.
- `runpodctl` and API v2 can stream logs, but the current bridge runner does not yet expose v2 log streaming.
- Workload progress must come from a heartbeat, status packet, or declared output.
- Logs are diagnostic evidence; they are not completion proof.

## Storage and egress

Use the smallest egress path that still provides durable verification:

- sanitized smoke runs can use the bridge's bounded inspection packet;
- durable workloads can use an object-store upload, an attached network volume, or an explicitly configured transfer path;
- retained storage must be included in cost and cleanup reporting.

Never place credentials, presigned URLs, account identifiers, private source locations, or private data in a manifest. Use references resolved by the trusted runtime.

## Primary sources

- [RunPod release notes](https://docs.runpod.io/release-notes)
- [REST API v2 overview](https://docs.runpod.io/api-reference-v2/overview)
- [REST API v1 migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1)
- [API v2 Pod log stream](https://docs.runpod.io/api-reference-v2/pods/stream-pod-logs)
- [`runpodctl` releases](https://github.com/runpod/runpodctl/releases)
- [`runpodctl` v2.12.0](https://github.com/runpod/runpodctl/releases/tag/v2.12.0)
- [Official RunPod plugins](https://github.com/runpod/runpod-plugins-official)
- [RunPod agent skills](https://docs.runpod.io/get-started/agent-skills)

See [RunPod official surfaces](../runpod-official-surfaces.md), [observability](../runpod-observability-ladder.md), and [worker readiness](../runpod-worker-readiness.md) for narrower operational references.
