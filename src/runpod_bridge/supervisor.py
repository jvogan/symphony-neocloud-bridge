from __future__ import annotations

from pathlib import Path
from typing import Any

from .closeout import build_closeout, resolve_path
from .manifest import get_nested
from .monitor import inspect_execution


def supervise_execution(manifest: dict[str, Any], base_dir: str | Path = ".") -> dict[str, Any]:
    base = Path(base_dir)
    monitor = inspect_execution(manifest, base)
    closeout = build_closeout(manifest, base) if monitor["state"] in ("succeeded", "failed") else {}
    checkpoint = checkpoint_state(manifest, base)
    action = next_action(monitor, closeout, checkpoint)
    return {
        "run_id": manifest.get("run_id"),
        "state": monitor["state"],
        "action": action,
        "monitor": monitor,
        "checkpoint": checkpoint,
        "closeout": closeout,
    }


def checkpoint_state(manifest: dict[str, Any], base: Path) -> dict[str, Any]:
    policy = get_nested(manifest, ["workload", "checkpoint_policy"], {})
    if not isinstance(policy, dict) or not policy:
        return {"declared": False, "mode": ""}
    mode = str(policy.get("mode") or "")
    path = str(policy.get("path") or policy.get("directory") or "runpod-execution/checkpoints")
    resolved = resolve_path(base, path)
    files = []
    if resolved.is_dir():
        files = [str(item.relative_to(base)) for item in sorted(resolved.rglob("*")) if item.is_file()]
    elif resolved.is_file():
        files = [str(resolved.relative_to(base))]
    return {
        "declared": True,
        "mode": mode,
        "path": str(resolved),
        "present": bool(files),
        "file_count": len(files),
        "files": files[:50],
    }


def next_action(monitor: dict[str, Any], closeout: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    state = monitor.get("state")
    if state == "succeeded" and closeout.get("status") == "succeeded":
        return "closeout_and_cleanup"
    if state == "succeeded":
        return "verify_missing_artifacts"
    if state == "failed" and checkpoint.get("present"):
        return "recover_from_checkpoint"
    if state == "failed":
        return "collect_failure_packet_and_cleanup"
    if state == "silent_timeout" and checkpoint.get("present"):
        return "sync_checkpoint_then_cleanup_or_resume"
    if state == "silent_timeout":
        return "poll_pod_then_cleanup_if_dead"
    if state == "running":
        return "continue_monitoring"
    return "wait_for_first_status_or_heartbeat"
