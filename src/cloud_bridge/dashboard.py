from __future__ import annotations

from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any

from .cost import estimate_cost, float_or_none, parse_time, unwrap_remote_record
from .recovery import analyze_recovery, extract_create
from .util import redact


RECORD_NAMES = ("remote_run_record.json", "handoff_run_record.json", "runpod_resource_record.json")


def scan_dashboard_records(root: str | Path) -> list[dict[str, Any]]:
    base = Path(root)
    records: list[dict[str, Any]] = []
    for name in RECORD_NAMES:
        for path in sorted(base.rglob(name)):
            records.append(summarize_record(path))
    return records


def summarize_record(path: str | Path) -> dict[str, Any]:
    record_path = Path(path).resolve()
    try:
        record = json.loads(record_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {
            "path": str(record_path),
            "status": "unreadable",
            "error": str(exc),
            "risk": "high",
            "actions": ["inspect_record"],
            "recommended_commands": [f"python3 -m json.tool {str(record_path)!r}"],
        }
    remote = unwrap_remote_record(record)
    create = extract_create(remote, record)
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    verification = remote.get("verification", {}) if isinstance(remote.get("verification"), dict) else {}
    verification_status = verification.get("status", {}) if isinstance(verification.get("status"), dict) else {}
    closeout = verification.get("closeout", {}) if isinstance(verification.get("closeout"), dict) else {}
    artifacts = closeout.get("artifacts", []) if isinstance(closeout.get("artifacts"), list) else []
    pod = create.get("pod", {}) if isinstance(create.get("pod"), dict) else {}
    recovery = analyze_recovery(record_path)
    cost = estimate_record_cost(remote, record, pod, cleanup)
    return {
        "path": str(record_path),
        "action": record.get("action"),
        "run_id": remote.get("manifest_run_id") or record.get("run_id") or record.get("handoff_validation", {}).get("run_id", ""),
        "status": remote.get("status") or record.get("status"),
        "pod_id": create.get("pod_id") or record.get("response", {}).get("id", ""),
        "pod_name": pod.get("name") or record.get("request", {}).get("name", ""),
        "cost_per_hr": pod.get("adjustedCostPerHr") or pod.get("costPerHr"),
        "estimated_cost_usd": cost["amount_usd"],
        "runtime_seconds": cost["runtime_seconds"],
        "verification_ok": verification.get("ok"),
        "workload_status": verification_status.get("status"),
        "closeout_status": closeout.get("status"),
        "artifact_hash_count": sum(1 for item in artifacts if isinstance(item, dict) and item.get("sha256")),
        "cleanup_status": cleanup.get("status"),
        "cleanup_verified": recovery.get("cleanup_verified"),
        "cleanup_unverified": recovery.get("cleanup_unverified"),
        "lock_status": remote.get("launch_lock", {}).get("status") if isinstance(remote.get("launch_lock"), dict) else "",
        "lock_stale": recovery.get("lock", {}).get("stale"),
        "risk": recovery.get("risk"),
        "risk_reasons": recovery.get("risk_reasons", []),
        "actions": recovery.get("actions", []),
        "recommended_commands": recovery.get("recommended_commands", []),
    }


def write_dashboard(records: list[dict[str, Any]], out_path: str | Path) -> dict[str, Any]:
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    records = redact(records)  # the dashboard is the most shareable artifact - scrub before render
    html_text = render_dashboard_html(records)
    output.write_text(html_text)
    json_path = output.with_suffix(".json")
    json_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    return {"html": str(output), "json": str(json_path), "records": len(records), "summary": summarize_dashboard(records)}


def render_dashboard_html(records: list[dict[str, Any]]) -> str:
    summary = summarize_dashboard(records)
    rows = "\n".join(render_row(record) for record in records)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>RunPod Bridge Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0 20px; }}
    .metric {{ border: 1px solid #d8dee4; border-radius: 6px; padding: 10px 12px; min-width: 150px; }}
    .metric strong {{ display: block; font-size: 18px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #d8dee4; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .status-succeeded {{ color: #116329; font-weight: 600; }}
    .status-failed, .status-verification_failed, .status-cleanup_failed {{ color: #b42318; font-weight: 600; }}
    .risk-high {{ color: #b42318; font-weight: 700; }}
    .risk-medium {{ color: #9a6700; font-weight: 700; }}
    .risk-low {{ color: #116329; font-weight: 700; }}
    code {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    .path {{ color: #57606a; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>RunPod Bridge Dashboard</h1>
  <div class="summary">
    <div class="metric"><span>Records</span><strong>{esc(summary["records"])}</strong></div>
    <div class="metric"><span>High Risk</span><strong>{esc(summary["high_risk"])}</strong></div>
    <div class="metric"><span>Cleanup Needed</span><strong>{esc(summary["cleanup_needed"])}</strong></div>
    <div class="metric"><span>Cleanup Unverified</span><strong>{esc(summary["cleanup_unverified"])}</strong></div>
    <div class="metric"><span>Stale Locks</span><strong>{esc(summary["stale_locks"])}</strong></div>
    <div class="metric"><span>Estimated Total</span><strong>${esc(summary["estimated_cost_usd"])}</strong></div>
    <div class="metric"><span>Open Cost/hr</span><strong>${esc(summary["open_cost_per_hr"])}</strong></div>
  </div>
  <table>
    <thead><tr><th>Run</th><th>Status</th><th>Risk</th><th>Actions</th><th>Pod</th><th>Cost</th><th>Verify</th><th>Workload</th><th>Cleanup</th><th>Lock</th><th>Recommended</th><th>Record</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
"""


def render_row(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "")
    risk = str(record.get("risk") or "")
    actions = ", ".join(str(action) for action in record.get("actions", []) if action)
    commands = "<br>".join(f"<code>{esc(command)}</code>" for command in record.get("recommended_commands", [])[:3])
    return (
        "      <tr>"
        f"<td>{esc(record.get('run_id'))}</td>"
        f"<td class=\"status-{esc(status)}\">{esc(status)}</td>"
        f"<td class=\"risk-{esc(risk)}\">{esc(risk)}</td>"
        f"<td>{esc(actions)}</td>"
        f"<td>{esc(record.get('pod_id'))}<br>{esc(record.get('pod_name'))}</td>"
        f"<td>${esc(record.get('estimated_cost_usd'))}<br><span class=\"path\">{esc(record.get('cost_per_hr'))}/hr</span></td>"
        f"<td>{esc(record.get('verification_ok'))}</td>"
        f"<td>{esc(record.get('workload_status'))}<br><span class=\"path\">closeout={esc(record.get('closeout_status'))} hashes={esc(record.get('artifact_hash_count'))}</span></td>"
        f"<td>{esc(record.get('cleanup_status'))}<br><span class=\"path\">verified={esc(record.get('cleanup_verified'))}</span></td>"
        f"<td>{esc(record.get('lock_status'))}<br><span class=\"path\">stale={esc(record.get('lock_stale'))}</span></td>"
        f"<td>{commands}</td>"
        f"<td class=\"path\">{esc(record.get('path'))}</td>"
        "</tr>"
    )


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def summarize_dashboard(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "high_risk": sum(1 for record in records if record.get("risk") == "high"),
        "medium_risk": sum(1 for record in records if record.get("risk") == "medium"),
        "cleanup_needed": sum(1 for record in records if "cleanup_pod" in record.get("actions", [])),
        "cleanup_unverified": sum(1 for record in records if record.get("cleanup_unverified") is True),
        "stale_locks": sum(1 for record in records if record.get("lock_stale") is True),
        "estimated_cost_usd": round(sum(number_or_zero(record.get("estimated_cost_usd")) for record in records), 6),
        "open_cost_per_hr": round(
            sum(number_or_zero(record.get("cost_per_hr")) for record in records if open_cost_record(record)),
            6,
        ),
    }


def estimate_record_cost(
    remote: dict[str, Any],
    record: dict[str, Any],
    pod: dict[str, Any],
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    start = parse_time(str(pod.get("lastStartedAt") or remote.get("ts") or record.get("ts") or ""))
    end = parse_time(str(cleanup.get("ts") or record.get("ts") or ""))
    if start and (end is None or end < start):
        end = datetime.now(timezone.utc)
    return estimate_cost(pod, start, end)


def open_cost_record(record: dict[str, Any]) -> bool:
    if not record.get("pod_id"):
        return False
    if record.get("cleanup_verified") is True:
        return False
    status = str(record.get("status") or "")
    return status not in ("blocked", "blocked_handoff", "dry_run_request")


def number_or_zero(value: Any) -> float:
    parsed = float_or_none(value)
    return parsed if parsed is not None else 0.0
