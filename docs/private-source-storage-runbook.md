# Private Source And Storage Runbook

Use this when a workload must avoid public GitHub repos, public container images, or public artifact endpoints.

## Decision Matrix

| Need | Preferred Path | Notes |
| --- | --- | --- |
| Sanitized tiny smoke | `repo.source: inline_commands` | Small only. No private data. |
| Private source without git credentials in pod | `repo.source: prepared_snapshot` + `repo.snapshot.archive_pod_path` | Stage `source_snapshot.tar.gz` onto a RunPod network volume through the S3-compatible API, then attach the volume. |
| Private source with existing object store | `repo.source: prepared_snapshot` + `archive_url_ref` | Inject a short-lived archive URL at launch. Do not commit presigned URLs. |
| Private Git repo | `repo.source: git_remote` | Requires immutable commit SHA, source proof, image with `git`, and runtime git credentials in the pod. Prefer snapshots when credentials are awkward. |
| Private container image | `runpod.containerRegistryAuthId` | Create RunPod registry auth on the trusted host or console. Local Docker/GitHub login does not prove RunPod can pull. |
| Durable artifacts without pod credentials | `artifact_egress.mode: aws_s3_presigned_upload` | Trusted orchestrator injects short-lived PUT URLs. |
| Durable artifacts on RunPod storage | `artifact_egress.mode: runpod_network_volume_s3` | Poll the declared archive through RunPod's S3-compatible API, verify locally, then clean up the pod. |
| Large retained datasets/checkpoints | `network_volume` or `runpod_network_volume_s3` | Secure Cloud only for Pods; record retention owner and billing. |

## RunPod Network-Volume Snapshot Source

This is the cleanest no-public-repo route for pod workloads:

1. Prepare a packet locally:

```bash
bin/cloud-bridge prepare path/to/launch_manifest.json \
  --source-dir /path/to/private/repo \
  --source-archive-pod-path /workspace/.runpod-bridge/source_snapshot.tar.gz \
  --out-dir .runtime/private-source-packet
```

2. Upload the generated source archive to the RunPod network volume from the trusted orchestrator host:

```bash
bin/cloud-bridge source-ingress-plan \
  .runtime/private-source-packet/launch_manifest.json \
  --source-archive .runtime/private-source-packet/source_snapshot.tar.gz
```

The plan renders `aws s3 cp` and `aws s3api head-object` commands using the RunPod S3-compatible endpoint for the declared data center. Load the RunPod S3 API keys into `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from your secure local secret store on the orchestrator host.

Do not put these keys in manifests, Linear, logs, or repo files. They are separate from `RUNPOD_API_KEY`.

When `--source-archive` points to an existing local file, `source-ingress-plan` computes its SHA-256 and fails if it does not match `repo.snapshot.archive_sha256`. Treat that as a hard stop; regenerate the packet or update the manifest from the trusted source archive metadata.

Review `.runtime/private-source-packet/source_snapshot.json` before launch. The bridge records excluded secret-like paths, included entry counts, archive SHA-256, and `personal_path_matches` such as hardcoded local home-directory strings found in text files. Treat personal path matches as a portability and privacy warning; either remove them from the source packet or document why they are harmless synthetic fixture text.

3. Launch with the prepared manifest after normal gates pass. The startup bootstrap verifies `repo.snapshot.archive_sha256`, unpacks the mounted archive into `repo.workdir`, and then runs the declared workload commands.

## Private Image Policy

Use public images only for sanitized smokes or official base images that contain no project code. For private workload images:

- Push the image to a private registry.
- Create RunPod container registry auth through the console, REST API, or `runpodctl registry create`.
- Put only the resulting `runpod.containerRegistryAuthId` in the manifest.
- Pin image tags or digests and run an image pull canary before the first paid workload.

Render the non-mutating provider-auth plan before launch:

```bash
bin/cloud-bridge registry-auth-plan path/to/launch_manifest.json
```

RunPod can create container registry auth records through `POST /containerregistryauth`; pod create accepts `containerRegistryAuthId`. Treat the registry password as a secret and rotate/delete stale auth records after use when possible.

## RunPod S3 Path Rules

RunPod network volumes are mounted at `/workspace` for Pods by default. The S3 API maps that filesystem path to:

```text
/workspace/my-folder/file.txt
s3://NETWORK_VOLUME_ID/my-folder/file.txt
```

Use datacenter-specific endpoints, for example:

```bash
aws s3 cp --region US-KS-2 \
  --endpoint-url https://s3api-us-ks-2.runpod.io/ \
  source_snapshot.tar.gz \
  s3://NETWORK_VOLUME_ID/.runpod-bridge/source_snapshot.tar.gz
```

Avoid recursive listing as primary proof for large directories. Validate exact expected files with `head-object`, hashes, and declared artifact manifests.

For `artifact_egress.mode: runpod_network_volume_s3`, closeout requires the archive to be downloaded from the RunPod S3-compatible endpoint, extracted locally, and passed through normal artifact hashing. A retained volume alone is not enough:

```bash
bin/cloud-bridge verify-network-volume-s3 \
  path/to/launch_manifest.json \
  --out-dir .runtime/network-volume-s3-verify \
  --execute
```

`run-remote --verification-mode auto` selects this verifier for `runpod_network_volume_s3` manifests before cleanup, waits for the declared archive, verifies locally, and then runs normal cleanup. The final success state still requires both artifact retrieval and cleanup verification.

## Paid Launch Gate

Before paid launch, require:

- Source visibility declared: public-sanitized, private-git, prepared-snapshot, or mounted-network-volume snapshot.
- Source digest declared: immutable git SHA or `sha256:<source_archive_digest>`.
- Image visibility declared: official/public base only, or private image with `containerRegistryAuthId`.
- Artifact egress declared: workspace archive for smoke only, or durable egress for real/private workloads.
- Cleanup owner declared for retained network volumes.
