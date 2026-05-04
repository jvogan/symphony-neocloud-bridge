# Public Readiness Handoff

Date: 2026-04-30

## Context

This repository packages a reusable RunPod execution bridge for Symphony + Linear workflows. It is intentionally domain-agnostic: workload owners provide commands, validation checks, and artifacts through a launch manifest; the bridge owns remote execution mechanics.

## Current Capabilities

- local manifest validation and dry-run planning
- startup script rendering
- local startup-contract execution
- artifact hashing and closeout file generation
- workspace archive packet creation
- guarded RunPod REST pod creation, inspection, and cleanup
- Codex skill guidance for Symphony workers

## Release Gate

Before publishing:

```bash
bin/runpod-bridge public-audit
PYTHONPATH=src python3 -m unittest discover -s tests -v
bin/runpod-bridge validate-manifest templates/runpod-launch-manifest.template.json
```

Remote smoke tests must use an explicit spend ceiling and must clean up the pod:

```bash
bin/runpod-bridge create-pod path/to/launch_manifest.json --max-spend-usd 5
bin/runpod-bridge cleanup-pod POD_ID --action delete
```

## Live Smoke

A CPU-only inline public smoke was run on 2026-04-30 with `--max-spend-usd 5`.

- create request succeeded
- reported cost rate: `$0.06/hour`
- cleanup delete request succeeded
- follow-up list by smoke prefix returned no pods
- resource identifiers are intentionally omitted from this public log

A CPU-only proxy-matrix smoke was also run on 2026-04-30 with `--max-spend-usd 5`.

- create request succeeded
- reported cost rate: `$0.06/hour`
- workload used four shards and seven declared artifacts
- cleanup delete request succeeded and a follow-up list by smoke prefix returned no pods
- HTTP proxy artifact retrieval failed from this environment with HTTP 403 / error code 1010
- the result is recorded as a provider-proxy limitation; durable artifact proof should use SCP, network volume, or object-store egress
- resource identifiers are intentionally omitted from this public log

A follow-up CPU-only proxy-matrix TCP smoke was run on 2026-04-30 with `--max-spend-usd 5`.

- create request succeeded
- reported cost rate: `$0.06/hour`
- direct TCP packet verification succeeded
- seven declared artifacts plus status, heartbeat, log, archive, hashes, closeout, and `symphony_outcome.md` were fetched
- cleanup delete request succeeded and a follow-up list by smoke prefix returned no pods
- resource identifiers are intentionally omitted from this public log
