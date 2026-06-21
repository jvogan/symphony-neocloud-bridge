from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .manifest import build_plan, load_manifest, validate_manifest
from .remote_run import PacketVerifier, run_remote_flow
from .providers.runpod.rest import RunpodRestClient, build_remote_launch_preview
from .util import now, redact


HANDOFF_KIND = "symphony_runpod_provider_handoff"


def write_provider_handoff(
    manifest: dict[str, Any],
    *,
    manifest_path: str | Path,
    out_path: str | Path,
    reason: str = "prepared_launch_packet",
    worker_id: str = "",
    local_preflight_path: str | Path | None = None,
    startup_path: str | Path | None = None,
    source_archive_path: str | Path | None = None,
    source_archive_manifest_path: str | Path | None = None,
    verification_mode: str = "auto",
    port: int = 8000,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    cleanup_action: str = "delete",
) -> dict[str, Any]:
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    handoff = build_provider_handoff(
        manifest,
        manifest_path=manifest_path,
        handoff_path=output,
        reason=reason,
        worker_id=worker_id,
        local_preflight_path=local_preflight_path,
        startup_path=startup_path,
        source_archive_path=source_archive_path,
        source_archive_manifest_path=source_archive_manifest_path,
        verification_mode=verification_mode,
        port=port,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        cleanup_action=cleanup_action,
    )
    output.write_text(json.dumps(redact(handoff), indent=2, sort_keys=True) + "\n")
    return handoff


def build_provider_handoff(
    manifest: dict[str, Any],
    *,
    manifest_path: str | Path,
    handoff_path: str | Path,
    reason: str,
    worker_id: str,
    local_preflight_path: str | Path | None,
    startup_path: str | Path | None,
    source_archive_path: str | Path | None,
    source_archive_manifest_path: str | Path | None,
    verification_mode: str,
    port: int,
    timeout_seconds: int,
    interval_seconds: int,
    cleanup_action: str,
) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    plan = build_plan(manifest, validation)
    launch_preview = build_remote_launch_preview(manifest)
    manifest_file = Path(manifest_path).resolve()
    handoff_file = Path(handoff_path).resolve()
    provider = str(manifest.get("provider", {}).get("name", "runpod") if isinstance(manifest.get("provider"), dict) else "runpod")
    status = "ready_for_orchestrator" if launch_preview["remote_ready"] else "blocked"
    files = {
        "launch_manifest": relative_or_absolute(manifest_file, handoff_file.parent),
        "local_preflight": relative_or_absolute(Path(local_preflight_path).resolve(), handoff_file.parent) if local_preflight_path else "",
        "startup": relative_or_absolute(Path(startup_path).resolve(), handoff_file.parent) if startup_path else "",
        "source_archive": relative_or_absolute(Path(source_archive_path).resolve(), handoff_file.parent) if source_archive_path else "",
        "source_archive_manifest": relative_or_absolute(Path(source_archive_manifest_path).resolve(), handoff_file.parent) if source_archive_manifest_path else "",
    }
    return {
        "schema_version": 1,
        "handoff_kind": HANDOFF_KIND,
        "provider": provider,
        "created_at": now(),
        "status": status,
        "reason": reason,
        "remote_execution_by": "orchestrator",
        "run_id": manifest.get("run_id"),
        "resource_name_prefix": get_nested(manifest, ["worker_coordination", "resource_name_prefix"], ""),
        "worker": {
            "id": worker_id,
            "role": "validator",
            "may_create_paid_resources": False,
        },
        "files": files,
        "manifest": {
            "sha256": file_sha256(manifest_file),
            "validation": validation.as_dict(),
            "plan": plan,
            "launch_preview": launch_preview,
        },
        "remote_execution": {
            "verification_mode": verification_mode,
            "port": port,
            "timeout_seconds": timeout_seconds,
            "interval_seconds": interval_seconds,
            "cleanup_action": cleanup_action,
        },
        "linear_closeout": {
            "remote_execution_by": "orchestrator",
            "claim_level": "artifact_execution_only",
            "requires_cleanup_status": True,
            "requires_artifact_hashes": True,
        },
    }


def load_provider_handoff(path: str | Path) -> dict[str, Any]:
    handoff_path = Path(path)
    data = json.loads(handoff_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{handoff_path}: handoff root must be an object")
    return data


def validate_provider_handoff(handoff: dict[str, Any], *, handoff_path: str | Path | None = None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if handoff.get("schema_version") != 1:
        error("schema_version", "must be 1")
    if handoff.get("handoff_kind") != HANDOFF_KIND:
        error("handoff_kind", f"must be {HANDOFF_KIND}")
    if handoff.get("provider") != "runpod":
        error("provider", "only runpod handoffs are supported")
    if handoff.get("remote_execution_by") != "orchestrator":
        error("remote_execution_by", "must be orchestrator")

    manifest_path = resolve_handoff_file(handoff, "launch_manifest", handoff_path)
    manifest: dict[str, Any] = {}
    if not manifest_path:
        error("files.launch_manifest", "required")
    elif not manifest_path.is_file():
        error("files.launch_manifest", f"not found: {manifest_path}")
    else:
        manifest = load_manifest(manifest_path)
        expected_sha = str(get_nested(handoff, ["manifest", "sha256"], "") or "")
        actual_sha = file_sha256(manifest_path)
        if expected_sha != actual_sha:
            error("manifest.sha256", "does not match launch_manifest")
        validation = validate_manifest(manifest)
        plan = build_plan(manifest, validation)
        launch_preview = build_remote_launch_preview(manifest)
        if not validation.ok:
            error("manifest.validation", "launch manifest is invalid")
        if not launch_preview["remote_ready"]:
            error("manifest.launch_preview.remote_ready", "launch manifest is not launch-ready")

    remote_execution = handoff.get("remote_execution", {})
    if not isinstance(remote_execution, dict):
        error("remote_execution", "required object")
    else:
        if remote_execution.get("verification_mode") not in ("auto", "tcp", "proxy", "none"):
            error("remote_execution.verification_mode", "must be auto, tcp, proxy, or none")
        if remote_execution.get("cleanup_action") not in ("stop", "delete"):
            error("remote_execution.cleanup_action", "must be stop or delete")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "manifest_path": str(manifest_path) if manifest_path else "",
        "run_id": manifest.get("run_id") if manifest else handoff.get("run_id"),
        "status": handoff.get("status"),
    }


def run_handoff_flow(
    handoff_path: str | Path,
    *,
    out_dir: str | Path,
    execute: bool,
    max_spend_usd: float | None = None,
    allow_duplicate: bool = False,
    verification_mode: str | None = None,
    port: int | None = None,
    timeout_seconds: int | None = None,
    interval_seconds: int | None = None,
    cleanup_action: str | None = None,
    cleanup_wait: bool = True,
    cleanup_timeout_seconds: int = 120,
    lock_dir: str | Path | None = None,
    client: RunpodRestClient | None = None,
    packet_verifier: PacketVerifier | None = None,
) -> dict[str, Any]:
    source = Path(handoff_path).resolve()
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    handoff = load_provider_handoff(source)
    validation = validate_provider_handoff(handoff, handoff_path=source)
    record: dict[str, Any] = {
        "ts": now(),
        "action": "run_handoff",
        "execute": execute,
        "handoff_path": str(source),
        "handoff_sha256": file_sha256(source),
        "handoff_validation": validation,
        "remote_execution_by": handoff.get("remote_execution_by"),
        "remote_run": {},
        "status": "started",
    }
    write_handoff_run_record(output, record)
    if not validation["ok"]:
        record["status"] = "blocked_handoff"
        write_handoff_run_record(output, record)
        return record

    manifest_path = Path(validation["manifest_path"])
    manifest = load_manifest(manifest_path)
    remote_execution = handoff.get("remote_execution", {}) if isinstance(handoff.get("remote_execution"), dict) else {}
    remote_record = run_remote_flow(
        manifest,
        out_dir=output / "remote",
        execute=execute,
        max_spend_usd=max_spend_usd,
        allow_duplicate=allow_duplicate,
        verification_mode=verification_mode or str(remote_execution.get("verification_mode") or "auto"),
        port=port if port is not None else int(remote_execution.get("port") or 8000),
        timeout_seconds=timeout_seconds if timeout_seconds is not None else int(remote_execution.get("timeout_seconds") or 180),
        interval_seconds=interval_seconds if interval_seconds is not None else int(remote_execution.get("interval_seconds") or 5),
        cleanup_action=cleanup_action or str(remote_execution.get("cleanup_action") or "delete"),
        cleanup_wait=cleanup_wait,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        lock_dir=lock_dir,
        client=client,
        packet_verifier=packet_verifier,
    )
    record["remote_run"] = remote_record
    record["status"] = str(remote_record.get("status") or "remote_run_unknown")
    write_handoff_run_record(output, record)
    return record


def write_handoff_run_record(output: Path, record: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "handoff_run_record.json").write_text(json.dumps(redact(record), indent=2, sort_keys=True) + "\n")


def resolve_handoff_file(handoff: dict[str, Any], key: str, handoff_path: str | Path | None) -> Path | None:
    files = handoff.get("files", {})
    if not isinstance(files, dict):
        return None
    value = str(files.get(key) or "")
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    if handoff_path is None:
        return path
    return Path(handoff_path).resolve().parent / path


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_nested(obj: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
