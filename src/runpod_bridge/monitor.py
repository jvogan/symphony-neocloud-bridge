from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .closeout import resolve_path


def inspect_execution(manifest: dict[str, Any], base_dir: str | Path = ".") -> dict[str, Any]:
    base = Path(base_dir)
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    monitoring = manifest.get("monitoring", {}) if isinstance(manifest.get("monitoring"), dict) else {}
    status_path = resolve_path(base, startup.get("status_file", "runpod-execution/status.json"))
    heartbeat_path = resolve_path(base, startup.get("heartbeat_file", "runpod-execution/monitor_events.ndjson"))
    log_path = resolve_path(base, startup.get("log_file", "runpod-execution/logs/startup.log"))

    last_heartbeat = read_last_json_line(heartbeat_path)
    status = read_json(status_path)
    max_silent_minutes = float(monitoring.get("max_silent_minutes") or 10)
    silence = silence_state(last_heartbeat, max_silent_minutes)
    productivity = productivity_state(status, last_heartbeat, silence, log_path)

    if status.get("status") in ("succeeded", "success", "completed"):
        state = "succeeded"
    elif status.get("status") in ("failed", "error"):
        state = "failed"
    elif silence["timed_out"]:
        state = "silent_timeout"
    elif status:
        state = "running"
    else:
        state = "unknown"

    return {
        "state": state,
        "status_file": str(status_path),
        "heartbeat_file": str(heartbeat_path),
        "log_file": str(log_path),
        "status": status,
        "last_heartbeat": last_heartbeat,
        "silence": silence,
        "productivity": productivity,
        "files": {
            "status_present": status_path.is_file(),
            "heartbeat_present": heartbeat_path.is_file(),
            "log_present": log_path.is_file(),
            "log_bytes": log_path.stat().st_size if log_path.is_file() else 0,
        },
    }


def productivity_state(
    status: dict[str, Any],
    last_heartbeat: dict[str, Any],
    silence: dict[str, Any],
    log_path: Path,
) -> dict[str, Any]:
    status_value = str(status.get("status") or "")
    phase = str(last_heartbeat.get("phase") or status.get("phase") or "")
    heartbeat_fresh = bool(last_heartbeat) and silence.get("timed_out") is False
    log_bytes = log_path.stat().st_size if log_path.is_file() else 0
    signals: list[str] = []
    if status_value in ("running", "succeeded", "success", "completed"):
        signals.append(f"status:{status_value}")
    if heartbeat_fresh:
        signals.append("heartbeat:fresh")
    if phase:
        signals.append(f"phase:{phase}")
    if log_bytes > 0:
        signals.append("log:present")

    if status_value in ("succeeded", "success", "completed"):
        state = "completed"
        confidence = "high"
        productive = False
        reason = "workload completed; verify artifacts and cleanup"
    elif status_value in ("failed", "error"):
        state = "failed"
        confidence = "high"
        productive = False
        reason = "workload reported failure"
    elif heartbeat_fresh and status_value == "running":
        state = "productive"
        confidence = "high" if log_bytes > 0 else "medium"
        productive = True
        reason = "fresh workload heartbeat and running status"
    elif heartbeat_fresh:
        state = "probably_productive"
        confidence = "medium"
        productive = True
        reason = "fresh workload heartbeat without final status"
    elif status_value == "running":
        state = "unproven"
        confidence = "low"
        productive = False
        reason = "status says running but heartbeat freshness is not proven"
    else:
        state = "unknown"
        confidence = "none"
        productive = False
        reason = "no workload-level progress signal is available"

    return {
        "state": state,
        "productive": productive,
        "confidence": confidence,
        "reason": reason,
        "signals": signals,
        "log_bytes": log_bytes,
    }


def silence_state(last_heartbeat: dict[str, Any], max_silent_minutes: float) -> dict[str, Any]:
    ts = last_heartbeat.get("ts") if isinstance(last_heartbeat, dict) else None
    parsed = parse_utc_timestamp(ts) if isinstance(ts, str) else None
    if parsed is None:
        return {
            "timed_out": False,
            "minutes_since_heartbeat": None,
            "max_silent_minutes": max_silent_minutes,
            "reason": "no parseable heartbeat timestamp",
        }
    now = datetime.now(timezone.utc)
    minutes = (now - parsed).total_seconds() / 60.0
    return {
        "timed_out": minutes > max_silent_minutes,
        "minutes_since_heartbeat": round(minutes, 3),
        "max_silent_minutes": max_silent_minutes,
        "reason": "ok" if minutes <= max_silent_minutes else "silence timeout exceeded",
    }


def parse_utc_timestamp(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_last_json_line(path: Path) -> dict[str, Any]:
    try:
        lines = [line for line in path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return {}
    if not lines:
        return {}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"raw": lines[-1]}
    return data if isinstance(data, dict) else {"raw": data}
