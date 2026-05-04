from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cost import unwrap_remote_record
from .runpod_rest import RunpodRestClient, cleanup_pod_flow


def analyze_recovery(record_path: str | Path) -> dict[str, Any]:
    path = Path(record_path).resolve()
    record = json.loads(path.read_text())
    remote = unwrap_remote_record(record)
    create = remote.get("create", {}) if isinstance(remote.get("create"), dict) else {}
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    pod_id = str(create.get("pod_id") or "")
    status = str(remote.get("status") or record.get("status") or "")
    cleanup_status = str(cleanup.get("status") or "")
    actions: list[str] = []
    risk = "low"

    if pod_id and not cleanup_status and status not in ("blocked", "blocked_handoff", "dry_run_request"):
        actions.append("cleanup_pod")
        risk = "high"
    if cleanup_status == "failed":
        actions.append("retry_cleanup")
        risk = "high"
    if status in ("verification_failed", "verification_error") and cleanup_status in ("submitted", "already_absent"):
        actions.append("inspect_artifact_failure")
        risk = "medium"
    if status == "blocked_launch_lock":
        actions.append("inspect_launch_lock")
        risk = "medium"
    if not actions:
        actions.append("no_recovery_action")

    return {
        "record_path": str(path),
        "status": status,
        "pod_id": pod_id,
        "cleanup_status": cleanup_status,
        "risk": risk,
        "actions": actions,
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
    result["status"] = "cleanup_submitted" if cleanup.get("status") in ("submitted", "already_absent") else cleanup.get("status")
    return result
