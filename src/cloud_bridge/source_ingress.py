from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any

from .egress import env_name_from_ref, first_value, network_volume_key
from .launch_env import baked_image_source_aliases, git_source_aliases, snapshot_source_aliases
from .source_check import source_proof_report


def build_source_ingress_plan(
    manifest: dict[str, Any],
    *,
    source_archive_path: str | Path | None = None,
) -> dict[str, Any]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    source = str(repo.get("source") or "")
    blockers: list[str] = []
    warnings: list[str] = []
    required_env: list[str] = []
    commands: list[str] = []
    details: dict[str, Any] = {}
    mode = source or "unknown"

    if source in git_source_aliases():
        proof = source_proof_report(manifest)
        commands.append(
            f"git ls-remote --exit-code {shlex.quote(str(repo.get('url_or_path') or 'REPO_URL'))} "
            f"{shlex.quote(str(repo.get('commit_or_snapshot') or 'COMMIT_SHA'))}"
        )
        if manifest.get("remote_launch_allowed") is True and not proof.get("ok"):
            blockers.append("repo.source_proof is required before paid git launch")
        warnings.append("private git sources require git credentials inside the pod; prefer prepared_snapshot for private repos")
        return result(mode, blockers, warnings, required_env, commands, details)

    if source == "inline_commands":
        warnings.append("inline_commands avoids source ingress but is restricted to small smoke workloads")
        return result(mode, blockers, warnings, required_env, commands, details)

    if source in baked_image_source_aliases():
        details["image_source"] = str(runpod.get("imageName") or "")
        warnings.append("source is baked into the declared container image; prove pullability with provider registry auth and an exact image canary")
        return result(mode, blockers, warnings, required_env, commands, details)

    if source == "local_snapshot":
        commands.append(
            "bin/cloud-bridge prepare <manifest> --source-dir <repo> "
            "--source-archive-pod-path /workspace/.runpod-bridge/source_snapshot.tar.gz "
            "--out-dir .runtime/<run-id>-packet"
        )
        blockers.append("local_snapshot must be converted to prepared_snapshot before remote launch")
        return result(mode, blockers, warnings, required_env, commands, details)

    if source not in snapshot_source_aliases():
        blockers.append(f"unsupported repo.source for source ingress: {source or '<missing>'}")
        return result(mode, blockers, warnings, required_env, commands, details)

    snapshot = repo.get("snapshot", {}) if isinstance(repo.get("snapshot"), dict) else {}
    archive_ref = str(snapshot.get("archive_url_ref") or "")
    archive_url = str(snapshot.get("archive_url") or "")
    archive_pod_path = str(snapshot.get("archive_pod_path") or "")
    archive_sha = str(snapshot.get("archive_sha256") or "")

    if archive_ref or archive_url:
        mode = "prepared_snapshot_url"
        env_name = env_name_from_ref(archive_ref, "RUNPOD_SOURCE_ARCHIVE_URL") if archive_ref else ""
        if env_name:
            required_env.append(env_name)
            commands.append(f"export {env_name}=<short-lived archive URL>")
        commands.append("bin/cloud-bridge validate-manifest <prepared-launch-manifest>")
        warnings.append("archive URL refs keep private source out of the repo; inject URLs only on the trusted orchestrator")
        return result(mode, blockers, warnings, required_env, commands, details)

    if archive_pod_path:
        mode = "runpod_network_volume_snapshot"
        network_volume_id = str(runpod.get("networkVolumeId") or "")
        if not network_volume_id:
            blockers.append("runpod.networkVolumeId is required for archive_pod_path source staging")
        if runpod.get("cloudType") != "SECURE":
            blockers.append("RunPod network volumes for Pods require Secure Cloud")
        datacenter = str(first_value(runpod.get("dataCenterIds")) or snapshot.get("data_center_id") or "")
        endpoint = str(snapshot.get("s3_endpoint_url") or "")
        endpoint_ref = str(snapshot.get("s3_endpoint_url_ref") or "")
        if endpoint_ref:
            endpoint_env = env_name_from_ref(endpoint_ref, "RUNPOD_S3_ENDPOINT_URL")
            required_env.append(endpoint_env)
            endpoint = f"${endpoint_env}"
        elif not endpoint and datacenter:
            endpoint = f"https://s3api-{datacenter.lower()}.runpod.io/"
        if not datacenter:
            blockers.append("runpod.dataCenterIds[0] or repo.snapshot.data_center_id is required for RunPod S3 source staging")
            datacenter = "DATACENTER"
        if not endpoint:
            blockers.append("repo.snapshot.s3_endpoint_url or s3_endpoint_url_ref is required when no data center is declared")
            endpoint = "https://s3api-DATACENTER.runpod.io/"
        required_env.extend(["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
        source_archive = str(source_archive_path or "source_snapshot.tar.gz")
        archive_file = Path(source_archive).expanduser()
        if source_archive_path and archive_file.is_file():
            digest = sha256_file(archive_file)
            details["source_archive_sha256"] = digest
            details["source_archive_size_bytes"] = archive_file.stat().st_size
            if archive_sha and digest != archive_sha:
                blockers.append("local source archive SHA-256 does not match repo.snapshot.archive_sha256")
        elif source_archive_path:
            warnings.append("source archive path does not exist locally yet; run prepare before staging")
        remote_key = network_volume_key(archive_pod_path, str(runpod.get("volumeMountPath") or "/workspace"))
        commands.append(
            f"aws s3 cp --region {shell_word(datacenter)} --endpoint-url {shell_word(endpoint)} "
            f"{shell_word(source_archive)} {s3_uri(network_volume_id or 'NETWORK_VOLUME_ID', remote_key)}"
        )
        commands.append(
            f"aws s3api head-object --region {shell_word(datacenter)} --endpoint-url {shell_word(endpoint)} "
            f"--bucket {shell_word(network_volume_id or 'NETWORK_VOLUME_ID')} --key {shell_word(remote_key)}"
        )
        if archive_sha:
            commands.append(f"sha256sum {shell_word(source_archive)}")
            warnings.append("compare source archive SHA-256 with repo.snapshot.archive_sha256 before launch")
        warnings.append("RunPod S3 API keys are separate from RUNPOD_API_KEY and must stay on the trusted orchestrator host")
        warnings.append("RunPod S3 has no presigned URL path; mounted network-volume snapshots avoid putting source credentials in the pod")
        return result(mode, blockers, warnings, required_env, commands, details)

    blockers.append("prepared_snapshot requires archive_url_ref, archive_url, or archive_pod_path")
    return result(mode, blockers, warnings, required_env, commands, details)


def result(
    mode: str,
    blockers: list[str],
    warnings: list[str],
    required_env: list[str],
    commands: list[str],
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = {
        "ok": not blockers,
        "mode": mode,
        "blockers": blockers,
        "warnings": warnings,
        "required_env": sorted(set(required_env)),
        "commands": commands,
    }
    if details:
        output.update(details)
    return output


def shell_word(value: str) -> str:
    if value.startswith("$") and value[1:].replace("_", "").isalnum():
        return value
    return shlex.quote(value)


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key.lstrip('/')}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
