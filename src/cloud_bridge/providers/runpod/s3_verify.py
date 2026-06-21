from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from typing import Any, Callable

from cloud_bridge.closeout import packet_relative_path, write_closeout_files
from cloud_bridge.egress import first_value, network_volume_key


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def verify_network_volume_s3(
    manifest: dict[str, Any],
    *,
    out_dir: str | Path,
    execute: bool = False,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = build_network_volume_s3_verify_plan(manifest, output)
    record: dict[str, Any] = {
        "ok": False,
        "status": "dry_run" if not execute else "started",
        "execute": execute,
        "run_id": manifest.get("run_id"),
        "plan": plan,
        "head_object": {},
        "head_attempts": [],
        "download": {},
        "archive_sha256": "",
        "closeout": {},
        "blockers": list(plan["blockers"]),
        "warnings": list(plan["warnings"]),
    }
    write_record(output, record)
    if plan["blockers"]:
        record["status"] = "blocked"
        write_record(output, record)
        return record
    if not execute:
        record["ok"] = True
        write_record(output, record)
        return record

    run = runner or default_runner
    missing_env = [str(plan["endpoint_env"])] if plan.get("endpoint_env") and not os.environ.get(str(plan["endpoint_env"])) else []
    if missing_env:
        record["status"] = "blocked_missing_env"
        record["blockers"].extend(f"missing required env for executed S3 verifier: {name}" for name in missing_env)
        write_record(output, record)
        return record
    deadline = time.monotonic() + timeout_seconds
    head: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        head = run_command(run, plan["head_command"])
        record["head_object"] = head
        record["head_attempts"].append(head)
        write_record(output, record)
        if head["ok"]:
            break
        sleep_seconds = min(interval_seconds, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)
    if not head.get("ok"):
        record["status"] = "head_object_timeout"
        write_record(output, record)
        return record

    download = run_command(run, plan["download_command"])
    record["download"] = download
    if not download["ok"]:
        record["status"] = "download_failed"
        write_record(output, record)
        return record

    archive_path = Path(plan["local_archive_path"])
    if not archive_path.is_file():
        record["status"] = "download_missing"
        record["blockers"].append("archive download command succeeded but local archive is missing")
        write_record(output, record)
        return record
    record["archive_sha256"] = sha256_file(archive_path)
    extracted = output / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_extract(tar, extracted)
    except (tarfile.TarError, OSError) as exc:
        record["status"] = "extract_failed"
        record["blockers"].append(str(exc))
        write_record(output, record)
        return record
    materialize_archive_for_closeout(manifest, extracted, archive_path)
    write_verified_egress_status(manifest, extracted, plan, record["archive_sha256"])
    closeout = write_closeout_files(manifest, extracted)
    record["closeout"] = closeout
    record["status"] = "succeeded" if closeout.get("status") == "succeeded" else "closeout_failed"
    record["ok"] = record["status"] == "succeeded"
    write_record(output, record)
    return record


def build_network_volume_s3_verify_plan(manifest: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    blockers: list[str] = []
    warnings: list[str] = []
    mode = str(egress.get("mode") or "")
    if mode != "runpod_network_volume_s3":
        blockers.append("artifact_egress.mode must be runpod_network_volume_s3 for this verifier")

    volume_id = str(runpod.get("networkVolumeId") or "")
    if not volume_id:
        blockers.append("runpod.networkVolumeId is required")
    datacenter = str(egress.get("data_center_id") or first_value(runpod.get("dataCenterIds")) or "")
    endpoint = str(egress.get("s3_endpoint_url") or "")
    endpoint_ref = str(egress.get("s3_endpoint_url_ref") or "")
    required_env = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    uses_env_refs = False
    endpoint_env = ""
    if endpoint_ref.startswith("env:"):
        uses_env_refs = True
        endpoint_env = endpoint_ref.split(":", 1)[1]
        if endpoint_env:
            required_env.append(endpoint_env)
        endpoint = os.environ.get(endpoint_env, f"${endpoint_env}")
        warnings.append("endpoint uses an env ref; export it before executing the rendered command")
    elif not endpoint and datacenter:
        endpoint = f"https://s3api-{datacenter.lower()}.runpod.io/"
    if not datacenter:
        blockers.append("artifact_egress.data_center_id or runpod.dataCenterIds[0] is required")
        datacenter = "DATACENTER"
    if not endpoint:
        blockers.append("artifact_egress.s3_endpoint_url or s3_endpoint_url_ref is required when no data center is declared")
        endpoint = "https://s3api-DATACENTER.runpod.io/"
    archive_path = str(egress.get("archive_path") or "runpod-execution/artifacts/runpod-execution.tar.gz")
    remote_key = str(
        egress.get("remote_archive_key")
        or network_volume_key(archive_path, str(runpod.get("volumeMountPath") or "/workspace"))
    )
    local_archive = out_dir / "network-volume-archive.tar.gz"
    head_command = [
        "aws",
        "s3api",
        "head-object",
        "--region",
        datacenter,
        "--endpoint-url",
        endpoint,
        "--bucket",
        volume_id or "NETWORK_VOLUME_ID",
        "--key",
        remote_key,
    ]
    download_command = [
        "aws",
        "s3",
        "cp",
        "--region",
        datacenter,
        "--endpoint-url",
        endpoint,
        f"s3://{volume_id or 'NETWORK_VOLUME_ID'}/{remote_key}",
        str(local_archive),
    ]
    return {
        "mode": mode,
        "bucket": volume_id,
        "data_center_id": datacenter,
        "endpoint_url": endpoint,
        "endpoint_url_ref": endpoint_ref,
        "endpoint_env": endpoint_env,
        "remote_archive_key": remote_key,
        "local_archive_path": str(local_archive),
        "required_env": required_env,
        "uses_env_refs": uses_env_refs,
        "head_command": head_command,
        "download_command": download_command,
        "commands": [shell_join(head_command), shell_join(download_command)],
        "blockers": blockers,
        "warnings": warnings,
        "docs": "https://docs.runpod.io/storage/s3-api",
        "expected_artifacts": [
            str(item.get("path"))
            for item in manifest.get("expected_artifacts", [])
            if isinstance(item, dict) and item.get("path")
        ],
    }


def run_command(run: CommandRunner, command: list[str]) -> dict[str, Any]:
    try:
        completed = run(command)
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": str(exc), "command": command}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
    }


def default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def write_verified_egress_status(manifest: dict[str, Any], base_dir: Path, plan: dict[str, Any], archive_sha256: str) -> None:
    execution_dir = base_dir / "runpod-execution"
    execution_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "mode": "runpod_network_volume_s3",
        "status": "verified",
        "run_id": manifest.get("run_id"),
        "bucket": plan.get("bucket", ""),
        "key": plan.get("remote_archive_key", ""),
        "data_center_id": plan.get("data_center_id", ""),
        "endpoint_url": plan.get("endpoint_url", ""),
        "archive_sha256": archive_sha256,
        "proof": "downloaded_from_runpod_network_volume_s3_and_extracted_for_closeout",
    }
    (execution_dir / "egress_status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def materialize_archive_for_closeout(manifest: dict[str, Any], base_dir: Path, archive_path: Path) -> None:
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    targets = []
    if egress.get("archive_path"):
        targets.append(egress["archive_path"])
    for artifact in manifest.get("expected_artifacts", []):
        if isinstance(artifact, dict) and artifact.get("path") and str(artifact.get("path", "")).endswith(".tar.gz"):
            targets.append(artifact["path"])
    for target in targets:
        local_target = base_dir / packet_relative_path(target)
        if local_target.resolve() == archive_path.resolve() or local_target.exists():
            continue
        local_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_path, local_target)


def safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    dest = destination.resolve()
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir()):
            raise tarfile.TarError(f"unsafe archive member type: {member.name}")
        target = (dest / member.name).resolve()
        if dest != target and dest not in target.parents:
            raise tarfile.TarError(f"unsafe archive member path: {member.name}")
    tar.extractall(dest)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shell_join(command: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in command)


def write_record(output: Path, record: dict[str, Any]) -> None:
    (output / "network_volume_s3_verify.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
