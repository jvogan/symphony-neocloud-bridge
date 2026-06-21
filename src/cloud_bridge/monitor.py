from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .closeout import resolve_path


def inspect_execution(
    manifest: dict[str, Any],
    base_dir: str | Path = ".",
    *,
    previous_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = Path(base_dir)
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    monitoring = manifest.get("monitoring", {}) if isinstance(manifest.get("monitoring"), dict) else {}
    status_path = resolve_path(base, startup.get("status_file", "runpod-execution/status.json"))
    heartbeat_path = resolve_path(base, startup.get("heartbeat_file", "runpod-execution/monitor_events.ndjson"))
    log_path = resolve_path(base, startup.get("log_file", "runpod-execution/logs/startup.log"))
    hash_path = resolve_path(base, "runpod-execution/artifact_hashes.jsonl")

    last_heartbeat = read_last_json_line(heartbeat_path)
    status = read_json(status_path)
    max_silent_minutes = float(monitoring.get("max_silent_minutes") or 10)
    heartbeat_required = monitoring.get("requires_workload_heartbeat") is True
    silence = silence_state(last_heartbeat, max_silent_minutes, heartbeat_required=heartbeat_required)
    files = {
        "status_present": status_path.is_file(),
        "heartbeat_present": heartbeat_path.is_file(),
        "log_present": log_path.is_file(),
        "hash_present": hash_path.is_file(),
        "log_bytes": log_path.stat().st_size if log_path.is_file() else 0,
        "hash_bytes": hash_path.stat().st_size if hash_path.is_file() else 0,
    }
    advancement = advancement_state(status, last_heartbeat, files, previous_report)
    productivity = productivity_state(status, last_heartbeat, silence, files, advancement)

    terminal_status = str(status.get("status") or "")
    workload_terminal_reported = terminal_status in ("succeeded", "success", "completed")
    if workload_terminal_reported:
        state = "terminal_reported"
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
        "workload_terminal_reported": workload_terminal_reported,
        "workload_terminal_status": terminal_status if workload_terminal_reported else "",
        "final_success": False,
        "final_success_reason": "monitor only inspects workload status; run closeout and verify cleanup before reporting final success",
        "status_file": str(status_path),
        "heartbeat_file": str(heartbeat_path),
        "log_file": str(log_path),
        "status": status,
        "last_heartbeat": last_heartbeat,
        "silence": silence,
        "advancement": advancement,
        "productivity": productivity,
        "files": files,
    }


def productivity_state(
    status: dict[str, Any],
    last_heartbeat: dict[str, Any],
    silence: dict[str, Any],
    files: dict[str, Any],
    advancement: dict[str, Any],
) -> dict[str, Any]:
    status_value = str(status.get("status") or "")
    phase = str(last_heartbeat.get("phase") or status.get("phase") or "")
    heartbeat_fresh = bool(last_heartbeat) and silence.get("timed_out") is False
    log_bytes = int(files.get("log_bytes") or 0)
    signals: list[str] = []
    if status_value in ("running", "succeeded", "success", "completed"):
        signals.append(f"status:{status_value}")
    if heartbeat_fresh:
        signals.append("harness_heartbeat:fresh")
    if phase:
        signals.append(f"phase:{phase}")
    if log_bytes > 0:
        signals.append("log:present")
    signals.extend(advancement.get("evidence", []))

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
    elif heartbeat_fresh and status_value == "running" and advancement.get("workload_advanced"):
        state = "workload_progressing"
        confidence = "high"
        productive = True
        reason = "workload evidence advanced since the previous monitor sample"
    elif heartbeat_fresh and status_value == "running" and advancement.get("harness_advanced"):
        state = "harness_progress_unproven"
        confidence = "medium"
        productive = False
        reason = "bridge harness advanced, but no workload-specific progress evidence advanced"
    elif heartbeat_fresh:
        state = "harness_alive_unproven"
        confidence = "low"
        productive = False
        reason = "fresh bridge heartbeat proves the harness is alive, not workload progress"
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
        "requires_advancement": True,
    }


def advancement_state(
    status: dict[str, Any],
    last_heartbeat: dict[str, Any],
    files: dict[str, Any],
    previous_report: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence: list[str] = []
    workload_advanced = False
    harness_advanced = False

    if not previous_report:
        return {
            "advanced": False,
            "workload_advanced": False,
            "harness_advanced": False,
            "evidence": [],
            "reason": "no previous monitor snapshot; one sample can prove liveness but not advancement",
        }

    previous_files = previous_report.get("files", {}) if isinstance(previous_report.get("files"), dict) else {}
    previous_status = previous_report.get("status", {}) if isinstance(previous_report.get("status"), dict) else {}
    previous_heartbeat = (
        previous_report.get("last_heartbeat", {}) if isinstance(previous_report.get("last_heartbeat"), dict) else {}
    )

    current_log_bytes = int(files.get("log_bytes") or 0)
    previous_log_bytes = int(previous_files.get("log_bytes") or 0)
    if current_log_bytes > previous_log_bytes:
        workload_advanced = True
        evidence.append(f"log_bytes_increased:{previous_log_bytes}->{current_log_bytes}")

    current_hash_bytes = int(files.get("hash_bytes") or 0)
    previous_hash_bytes = int(previous_files.get("hash_bytes") or 0)
    if current_hash_bytes > previous_hash_bytes:
        workload_advanced = True
        evidence.append(f"hash_ledger_grew:{previous_hash_bytes}->{current_hash_bytes}")

    if comparable_status(status) != comparable_status(previous_status):
        workload_advanced = True
        evidence.append("status_payload_changed")

    current_phase = str(last_heartbeat.get("phase") or "")
    previous_phase = str(previous_heartbeat.get("phase") or "")
    current_hb_status = str(last_heartbeat.get("status") or "")
    previous_hb_status = str(previous_heartbeat.get("status") or "")
    if (current_phase, current_hb_status) != (previous_phase, previous_hb_status):
        if current_phase == "heartbeat" and current_hb_status == "running":
            evidence.append("heartbeat_tick_only")
        else:
            harness_advanced = True
            evidence.append(f"harness_phase_changed:{previous_phase or '<none>'}->{current_phase or '<none>'}")

    return {
        "advanced": workload_advanced or harness_advanced,
        "workload_advanced": workload_advanced,
        "harness_advanced": harness_advanced,
        "evidence": evidence,
        "reason": "advancement detected" if (workload_advanced or harness_advanced) else "no workload advancement since previous monitor sample",
    }


def comparable_status(status: dict[str, Any]) -> dict[str, Any]:
    ignored = {"ts", "timestamp", "log_file", "hash_file"}
    return {key: value for key, value in status.items() if key not in ignored}


def silence_state(last_heartbeat: dict[str, Any], max_silent_minutes: float, *, heartbeat_required: bool = False) -> dict[str, Any]:
    ts = last_heartbeat.get("ts") if isinstance(last_heartbeat, dict) else None
    parsed = parse_utc_timestamp(ts) if isinstance(ts, str) else None
    if parsed is None:
        return {
            "timed_out": heartbeat_required,
            "invalid_timestamp": bool(ts),
            "minutes_since_heartbeat": None,
            "max_silent_minutes": max_silent_minutes,
            "reason": "required heartbeat is missing or unparseable" if heartbeat_required else "no parseable heartbeat timestamp",
        }
    now = datetime.now(timezone.utc)
    minutes = (now - parsed).total_seconds() / 60.0
    if minutes < -2:
        return {
            "timed_out": True,
            "invalid_timestamp": True,
            "minutes_since_heartbeat": round(minutes, 3),
            "max_silent_minutes": max_silent_minutes,
            "reason": "heartbeat timestamp is in the future beyond allowed clock skew",
        }
    return {
        "timed_out": minutes > max_silent_minutes,
        "invalid_timestamp": False,
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
