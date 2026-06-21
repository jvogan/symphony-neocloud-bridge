# Manifest Migration Guide

Use this when a downstream workload repo has older RunPod launch bundles or hand-written sidecar specs.

## First Command

```bash
bin/cloud-bridge audit-manifests <repo-or-file> --migration-hints --summary-only
```

Use the summary first for large repos. It groups repeated errors, warnings, and migration hints by count so the first cleanup wave is obvious. Re-run without `--summary-only`, or with `--only-failures`, when you need the exact files to edit.

Then run the normal chain on any candidate you plan to launch:

```bash
bin/cloud-bridge validate-manifest <manifest>
bin/cloud-bridge contract-self-check <manifest>
bin/cloud-bridge preflight <manifest>
bin/cloud-bridge egress-plan <manifest>
```

## Common Field Moves

| Old pattern | Current contract |
| --- | --- |
| top-level `stage_contract` | `workload.stage_contract` |
| `expected_outputs` | `expected_artifacts[]` with `artifact_id`, `path`, `required`, and `sha256_required` |
| `provider_handoff_policy` | `worker_coordination` plus `closeout` cleanup policy |
| `fallback_policy` | `workload.checkpoint_policy`, `monitoring`, and recovery runbook text |
| `repo.source: local_snapshot` for paid launch | `prepared_snapshot`, `object_store_archive`, or immutable `git_remote` |
| pod-side `egress_status: uploaded` as final proof | trusted verifier records `egress_status: verified` |

## Workload Repo Patterns

Private source snapshot:

- Package committed code with `git archive` or `prepare --source-dir`.
- Stage it behind a short-lived URL or object-store ref.
- Use `repo.source: prepared_snapshot` or `object_store_archive`.
- Require `snapshot.archive_sha256`; do not rely on public GitHub just to move source.
- Check `source_snapshot.json.personal_path_matches` before launch so local home-directory path strings do not leak into remote packets or break on the pod.

GPU smoke before scale:

- Use a small base image or image-native canary before heavy CUDA images.
- Pin an explicit mid-range `gpuTypeIds` list instead of `[]`.
- Run `gpu-catalog --manifest <manifest>` for GPU manifests with explicit GPU IDs and data centers before retrying `no instances`; REST does not distinguish wrong-DC catalog mismatch from zero capacity.
- Give large image pulls a separate boot budget; a short wedge timer can kill a valid pull.
- Treat `desiredStatus=RUNNING` as provider intent, not workload proof.

Checkpointed large run:

- Declare `workload.checkpoint_policy`.
- Use durable egress: `runpod_network_volume_s3`, `network_volume`, `scp`, `object_store_upload`, or `aws_s3_presigned_upload`.
- Require live progress and artifact hash ledgers.

Network-volume/S3 egress:

- Secure Cloud is required for RunPod network volumes.
- RunPod S3 credentials are distinct from `RUNPOD_API_KEY`.
- Final success requires download/extract/hash verification, not just retention on the volume.

Linear outcome migration:

- Include `cleanup_status` and `cleanup_verified`.
- Include `progress_report_classification_state` verbatim.
- Include missing artifacts/evidence and egress verification fields.
- Do not move a RunPod issue to final success from pod creation, provider `RUNNING`, monitor heartbeat, or cleanup submission alone.
