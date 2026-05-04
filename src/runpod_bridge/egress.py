from __future__ import annotations

import re
from typing import Any

from .manifest import get_nested, has_port


def build_egress_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    access = manifest.get("access", {}) if isinstance(manifest.get("access"), dict) else {}
    mode = str(egress.get("mode") or "workspace_archive")
    blockers: list[str] = []
    warnings: list[str] = []
    required_env: list[str] = []
    commands: list[str] = []

    archive_path = str(egress.get("archive_path") or "runpod-execution/artifacts/runpod-execution.tar.gz")
    artifact_paths = [
        str(item.get("path"))
        for item in manifest.get("expected_artifacts", [])
        if isinstance(item, dict) and item.get("path")
    ]

    if mode == "workspace_archive":
        commands.append(f"tar -tzf {archive_path}")
        warnings.append("workspace_archive is proof packaging, not durable off-pod storage")
    elif mode == "network_volume":
        network_volume_id = str(runpod.get("networkVolumeId") or "")
        if not network_volume_id:
            blockers.append("runpod.networkVolumeId is required for network_volume egress")
        if runpod.get("cloudType") != "SECURE":
            blockers.append("RunPod network volumes for Pods require Secure Cloud")
        mount = str(runpod.get("volumeMountPath") or "/workspace")
        commands.append(f"test -d {mount}")
        commands.append(f"find {mount}/runpod-execution -maxdepth 3 -type f")
        warnings.append("network volumes are retained after pod deletion and need explicit cleanup ownership")
    elif mode == "runpod_network_volume_s3":
        network_volume_id = str(runpod.get("networkVolumeId") or "")
        if not network_volume_id:
            blockers.append("runpod.networkVolumeId is required for runpod_network_volume_s3 egress")
        if runpod.get("cloudType") != "SECURE":
            blockers.append("RunPod network volumes for Pods require Secure Cloud")
        datacenter = str(egress.get("data_center_id") or first_value(runpod.get("dataCenterIds")) or "")
        endpoint = str(egress.get("s3_endpoint_url") or "")
        endpoint_ref = str(egress.get("s3_endpoint_url_ref") or "")
        if not endpoint and datacenter:
            endpoint = f"https://s3api-{datacenter.lower()}.runpod.io/"
        if endpoint_ref.startswith("env:"):
            required_env.append(endpoint_ref.split(":", 1)[1])
            endpoint = f"${endpoint_ref.split(':', 1)[1]}"
        if not endpoint:
            blockers.append("runpod_network_volume_s3 requires artifact_egress.data_center_id, s3_endpoint_url, or s3_endpoint_url_ref")
        if not egress.get("credentials_ref"):
            warnings.append("runpod_network_volume_s3 should name a runtime RunPod S3 API key reference")
        required_env.extend(["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
        remote_key = str(egress.get("remote_archive_key") or network_volume_key(archive_path, str(runpod.get("volumeMountPath") or "/workspace")))
        commands.append(f"aws s3 cp --region {datacenter or 'DATACENTER'} --endpoint-url {endpoint or 'https://s3api-DATACENTER.runpod.io/'} s3://{network_volume_id or 'NETWORK_VOLUME_ID'}/{remote_key} ./artifacts/")
        commands.append(f"aws s3api head-object --region {datacenter or 'DATACENTER'} --endpoint-url {endpoint or 'https://s3api-DATACENTER.runpod.io/'} --bucket {network_volume_id or 'NETWORK_VOLUME_ID'} --key {remote_key}")
        warnings.append("RunPod S3 API keys are separate from RUNPOD_API_KEY and must not be stored in manifests or Linear")
    elif mode == "scp":
        if not access.get("full_ssh_scp_required"):
            blockers.append("scp egress requires access.full_ssh_scp_required=true")
        if not has_port([p for p in runpod.get("ports", []) if isinstance(p, str)], "tcp", 22):
            blockers.append("scp egress requires 22/tcp")
        if not runpod.get("supportPublicIp"):
            warnings.append("full SCP normally requires supportPublicIp=true")
        required_env.extend(["RUNPOD_SSH_HOST", "RUNPOD_SSH_PORT", "RUNPOD_SSH_USER"])
        commands.append(f"rsync -av --partial <pod>:{archive_path} ./artifacts/")
    elif mode == "object_store_upload":
        destination_uri = str(egress.get("destination_uri") or "")
        destination_ref = str(egress.get("destination_uri_ref") or "")
        if destination_uri:
            destination = destination_uri
        elif destination_ref.startswith("env:"):
            env_name = destination_ref.split(":", 1)[1]
            required_env.append(env_name)
            destination = f"${env_name}"
        else:
            required_env.append("RUNPOD_OBJECT_STORE_URI")
            destination = "$RUNPOD_OBJECT_STORE_URI"
        credential_ref = str(egress.get("credentials_ref") or "")
        if not credential_ref:
            warnings.append("object_store_upload should name a runtime credential reference")
        endpoint_ref = str(egress.get("s3_endpoint_url_ref") or "")
        if endpoint_ref:
            required_env.append("AWS_ENDPOINT_URL")
        commands.append(f"aws s3 cp {archive_path} {destination.rstrip('/')}/")
        commands.append(f"aws s3 cp runpod-execution/artifact_hashes.jsonl {destination.rstrip('/')}/")
    elif mode == "aws_s3_presigned_upload":
        archive_ref = str(egress.get("archive_upload_url_ref") or egress.get("upload_url_ref") or "")
        hash_ref = str(egress.get("hash_upload_url_ref") or "")
        literal_archive_url = str(egress.get("archive_upload_url") or egress.get("upload_url") or "")
        if literal_archive_url:
            blockers.append("aws_s3_presigned_upload must not store literal presigned URLs in the manifest")
        archive_env = env_name_from_ref(archive_ref, "RUNPOD_PRESIGNED_ARCHIVE_PUT_URL")
        hash_env = env_name_from_ref(hash_ref, "RUNPOD_PRESIGNED_HASH_PUT_URL")
        required_env.append(archive_env)
        if hash_ref:
            required_env.append(hash_env)
        if egress.get("requires_presigned_upload") is not True:
            warnings.append("aws_s3_presigned_upload should set requires_presigned_upload=true so missing upload URLs fail closed")
        commands.append(f'curl --fail --silent --show-error --upload-file {archive_path} "${archive_env}"')
        if hash_ref:
            commands.append(f'curl --fail --silent --show-error --upload-file runpod-execution/artifact_hashes.jsonl "${hash_env}"')
        else:
            warnings.append("hash_upload_url_ref is optional but recommended so closeout can verify the off-pod hash ledger")
        warnings.append("presigned S3 PUT URLs are bearer credentials; inject them at runtime and keep expiration short")
    else:
        blockers.append(f"unsupported artifact_egress.mode: {mode}")

    if mode in ("object_store_upload", "network_volume", "runpod_network_volume_s3", "aws_s3_presigned_upload") and get_nested(manifest, ["workload", "scale"], "") in ("large", "huge"):
        checkpoint = get_nested(manifest, ["workload", "checkpoint_policy", "mode"], "")
        if not checkpoint or checkpoint == "none":
            warnings.append("large/huge durable egress should pair with checkpoint_policy")

    return {
        "ok": not blockers,
        "mode": mode,
        "archive_path": archive_path,
        "artifact_paths": artifact_paths,
        "blockers": blockers,
        "warnings": warnings,
        "required_env": sorted(set(required_env)),
        "commands": commands,
        "durable": mode in ("network_volume", "runpod_network_volume_s3", "scp", "object_store_upload", "aws_s3_presigned_upload"),
    }


def first_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def network_volume_key(path: str, mount_path: str) -> str:
    normalized = path.lstrip("/")
    mount = mount_path.strip("/")
    if mount and normalized.startswith(f"{mount}/"):
        normalized = normalized[len(mount) + 1 :]
    return normalized


def env_name_from_ref(ref: str, default: str) -> str:
    if ref.startswith("env:"):
        value = ref.split(":", 1)[1].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return value
    return default
