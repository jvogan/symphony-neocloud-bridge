from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
from typing import Any

from .cost import parse_time, unwrap_remote_record
from .providers.runpod.rest import RunpodRestClient, cleanup_pod_flow


RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
NO_REMOTE_CLEANUP_STATUSES = {
    "blocked",
    "blocked_handoff",
    "blocked_launch_lock",
    "blocked_spend_ceiling",
    "dry_run_request",
    "not_executed",
}
VERIFIED_CLEANUP_STATUSES = {
    "already_absent",
    "deleted",
    "delete_verified",
    "stopped",
    "stop_verified",
    "verified",
    "verified_absent",
}


def analyze_recovery(record_path: str | Path, *, stale_lock_after_seconds: int = 7200) -> dict[str, Any]:
    path = Path(record_path).resolve()
    record = json.loads(path.read_text())
    remote = unwrap_remote_record(record)
    create = extract_create(remote, record)
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    verification = remote.get("verification", {}) if isinstance(remote.get("verification"), dict) else {}
    launch_lock = remote.get("launch_lock", {}) if isinstance(remote.get("launch_lock"), dict) else {}
    pod_id = str(create.get("pod_id") or "")
    status = str(remote.get("status") or record.get("status") or "")
    cleanup_status = str(cleanup.get("status") or "")
    cleanup_action = str(cleanup.get("action") or remote.get("cleanup_action") or "delete")
    cleanup_verified = is_cleanup_verified(cleanup)
    lock = analyze_launch_lock(launch_lock, stale_lock_after_seconds=stale_lock_after_seconds)
    actions: list[str] = []
    risk_reasons: list[str] = []
    recommended_commands: list[str] = []
    risk = "low"

    def add(action: str, level: str, reason: str, commands: list[str] | None = None) -> None:
        nonlocal risk
        if action not in actions:
            actions.append(action)
        if reason and reason not in risk_reasons:
            risk_reasons.append(reason)
        if RISK_ORDER[level] > RISK_ORDER[risk]:
            risk = level
        for command in commands or []:
            if command not in recommended_commands:
                recommended_commands.append(command)

    if pod_id and not cleanup_status and status not in NO_REMOTE_CLEANUP_STATUSES:
        add(
            "cleanup_pod",
            "high",
            "pod was created but no cleanup record is present",
            cleanup_commands(path, pod_id, cleanup_action),
        )
    if cleanup_status == "failed":
        add(
            "retry_cleanup",
            "high",
            "cleanup request failed",
            cleanup_commands(path, pod_id, cleanup_action) if pod_id else [],
        )
    if cleanup_status == "submitted" and not cleanup_verified:
        add(
            "verify_cleanup",
            "high",
            "cleanup was submitted but not verified; spend may still be active",
            verify_cleanup_commands(pod_id, cleanup_action),
        )
    if status in ("verification_failed", "verification_error"):
        add(
            "inspect_artifact_failure",
            "medium",
            "artifact verification failed or crashed",
            inspect_artifact_commands(path),
        )
    if status == "succeeded" and not verification_has_artifact_proof(verification):
        add(
            "inspect_unproven_success",
            "high",
            "remote run is marked succeeded but verifier output lacks terminal status plus required artifact hashes",
            inspect_artifact_commands(path),
        )
    if status in ("created_unverified", "created"):
        add(
            "inspect_unverified_run",
            "high" if pod_id and not cleanup_verified else "medium",
            "pod creation was recorded without artifact verification",
            verify_cleanup_commands(pod_id, cleanup_action) if pod_id and cleanup_status == "submitted" else [],
        )
    if status == "blocked_launch_lock":
        add(
            "inspect_launch_lock",
            "medium",
            "launch was blocked by an existing lock",
            lock_commands(lock),
        )
    if lock.get("open") and lock.get("stale"):
        add(
            "inspect_stale_launch_lock",
            "medium",
            "launch lock appears stale from record timestamp",
            lock_commands(lock),
        )
    elif lock.get("open") and not lock.get("released"):
        add(
            "inspect_launch_lock",
            "medium",
            "launch lock is not recorded as released",
            lock_commands(lock),
        )
    if not actions:
        actions.append("no_recovery_action")

    return {
        "record_path": str(path),
        "status": status,
        "pod_id": pod_id,
        "cleanup_status": cleanup_status,
        "cleanup_action": cleanup_action,
        "cleanup_verified": cleanup_verified,
        "cleanup_unverified": bool(cleanup_status == "submitted" and not cleanup_verified),
        "verification_ok": verification.get("ok"),
        "lock": lock,
        "risk": risk,
        "risk_reasons": risk_reasons,
        "actions": actions,
        "action_summary": ", ".join(actions),
        "recommended_commands": recommended_commands,
    }


def recover_run(
    record_path: str | Path,
    *,
    execute_cleanup: bool,
    cleanup_action: str = "delete",
    out_dir: str | Path | None = None,
    client: RunpodRestClient | None = None,
) -> dict[str, Any]:
    analysis = analyze_recovery(record_path)
    result: dict[str, Any] = {"analysis": analysis, "cleanup": {}}
    if "cleanup_pod" not in analysis["actions"] and "retry_cleanup" not in analysis["actions"]:
        result["status"] = "no_cleanup_needed"
        return result
    pod_id = analysis.get("pod_id")
    if not pod_id:
        result["status"] = "blocked_no_pod_id"
        return result
    output = Path(out_dir).resolve() if out_dir else Path(record_path).resolve().parent / "recovery"
    cleanup = cleanup_pod_flow(
        str(pod_id),
        out_dir=output,
        action=cleanup_action,
        execute=execute_cleanup,
        client=client,
    )
    result["cleanup"] = cleanup
    if cleanup.get("status") == "submitted":
        result["status"] = "cleanup_submitted"
    elif cleanup.get("status") == "already_absent":
        result["status"] = "cleanup_verified"
    else:
        result["status"] = cleanup.get("status")
    return result


def extract_create(remote: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    create = remote.get("create", {}) if isinstance(remote.get("create"), dict) else {}
    if create.get("pod_id") or create.get("pod"):
        return create
    response = record.get("response", {}) if isinstance(record.get("response"), dict) else {}
    pod_id = response.get("id") or response.get("pod_id")
    if not pod_id:
        return create
    return {"pod_id": pod_id, "pod": response}


def is_cleanup_verified(cleanup: dict[str, Any]) -> bool:
    status = str(cleanup.get("status") or "")
    verification = cleanup.get("verification", {}) if isinstance(cleanup.get("verification"), dict) else {}
    response = cleanup.get("response", {}) if isinstance(cleanup.get("response"), dict) else {}
    return bool(
        cleanup.get("verified") is True
        or cleanup.get("cleanup_verified") is True
        or verification.get("ok") is True
        or response.get("already_absent") is True
        or status in VERIFIED_CLEANUP_STATUSES
    )


def verification_has_artifact_proof(verification: dict[str, Any]) -> bool:
    if verification.get("ok") is not True:
        return False
    status = verification.get("status", {}) if isinstance(verification.get("status"), dict) else {}
    if str(status.get("status") or "") not in ("succeeded", "success", "completed"):
        return False
    closeout = verification.get("closeout", {}) if isinstance(verification.get("closeout"), dict) else {}
    if closeout.get("status") != "succeeded" or closeout.get("missing_required_artifacts"):
        return False
    artifacts = closeout.get("artifacts", [])
    if not isinstance(artifacts, list):
        return False
    required = [item for item in artifacts if isinstance(item, dict) and item.get("required") is True]
    return bool(required) and all(item.get("present") is True and item.get("sha256") for item in required)


def analyze_launch_lock(launch_lock: dict[str, Any], *, stale_lock_after_seconds: int) -> dict[str, Any]:
    if not launch_lock:
        return {"status": "", "open": False, "released": False, "stale": False}
    existing = launch_lock.get("existing", {}) if isinstance(launch_lock.get("existing"), dict) else {}
    holder = existing or launch_lock
    status = str(launch_lock.get("status") or "")
    released = launch_lock.get("released") is True or status == "released"
    open_lock = bool(status in ("held", "acquired") and not released)
    ts = parse_time(str(holder.get("ts") or launch_lock.get("ts") or ""))
    age_seconds = None
    stale = False
    if ts is not None:
        age_seconds = max(0, int((_utc_now() - ts).total_seconds()))
        stale = bool(open_lock and age_seconds >= stale_lock_after_seconds)
    return {
        "status": status,
        "open": open_lock,
        "released": released,
        "stale": stale,
        "age_seconds": age_seconds,
        "path": str(launch_lock.get("path") or ""),
        "key": str(launch_lock.get("key") or holder.get("key") or ""),
        "owner_id": str(holder.get("owner_id") or ""),
        "pid": holder.get("pid"),
        "run_id": str(holder.get("run_id") or ""),
        "out_dir": str(holder.get("out_dir") or ""),
    }


def cleanup_commands(record_path: Path, pod_id: str, cleanup_action: str) -> list[str]:
    if not pod_id:
        return []
    return [
        "bin/cloud-bridge recover-run "
        f"{shlex.quote(str(record_path))} --execute-cleanup --action {shlex.quote(cleanup_action)} --yes-cleanup-runpod",
        "bin/cloud-bridge cleanup-pod "
        f"{shlex.quote(pod_id)} --action {shlex.quote(cleanup_action)} --execute --yes-cleanup-runpod",
    ]


def verify_cleanup_commands(pod_id: str, cleanup_action: str) -> list[str]:
    if not pod_id:
        return []
    return [
        f"bin/cloud-bridge get-pod {shlex.quote(pod_id)} --json",
        "bin/cloud-bridge cleanup-pod "
        f"{shlex.quote(pod_id)} --action {shlex.quote(cleanup_action)} --execute --yes-cleanup-runpod",
    ]


def inspect_artifact_commands(record_path: Path) -> list[str]:
    packet_dir = record_path.parent / "packet"
    return [f"ls -la {shlex.quote(str(packet_dir))}"]


def lock_commands(lock: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    path = str(lock.get("path") or "")
    pid = lock.get("pid")
    if path:
        commands.append(f"cat {shlex.quote(path)}")
    if pid:
        commands.append(f"ps -p {shlex.quote(str(pid))}")
    if path and lock.get("stale"):
        commands.append(f"# after confirming no worker owns it: rm {shlex.quote(path)}")
    return commands


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
