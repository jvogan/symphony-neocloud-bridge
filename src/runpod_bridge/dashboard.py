from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .cost import unwrap_remote_record


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
        return {"path": str(record_path), "status": "unreadable", "error": str(exc)}
    remote = unwrap_remote_record(record)
    create = remote.get("create", {}) if isinstance(remote.get("create"), dict) else {}
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    verification = remote.get("verification", {}) if isinstance(remote.get("verification"), dict) else {}
    pod = create.get("pod", {}) if isinstance(create.get("pod"), dict) else {}
    return {
        "path": str(record_path),
        "action": record.get("action"),
        "run_id": remote.get("manifest_run_id") or record.get("run_id") or record.get("handoff_validation", {}).get("run_id", ""),
        "status": remote.get("status") or record.get("status"),
        "pod_id": create.get("pod_id") or record.get("response", {}).get("id", ""),
        "pod_name": pod.get("name") or record.get("request", {}).get("name", ""),
        "cost_per_hr": pod.get("adjustedCostPerHr") or pod.get("costPerHr"),
        "verification_ok": verification.get("ok"),
        "cleanup_status": cleanup.get("status"),
        "lock_status": remote.get("launch_lock", {}).get("status") if isinstance(remote.get("launch_lock"), dict) else "",
    }


def write_dashboard(records: list[dict[str, Any]], out_path: str | Path) -> dict[str, Any]:
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_dashboard_html(records)
    output.write_text(html_text)
    json_path = output.with_suffix(".json")
    json_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    return {"html": str(output), "json": str(json_path), "records": len(records)}


def render_dashboard_html(records: list[dict[str, Any]]) -> str:
    rows = "\n".join(render_row(record) for record in records)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>RunPod Bridge Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #d8dee4; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .status-succeeded {{ color: #116329; font-weight: 600; }}
    .status-failed, .status-verification_failed, .status-cleanup_failed {{ color: #b42318; font-weight: 600; }}
    .path {{ color: #57606a; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>RunPod Bridge Dashboard</h1>
  <table>
    <thead><tr><th>Run</th><th>Status</th><th>Pod</th><th>Cost/hr</th><th>Verify</th><th>Cleanup</th><th>Lock</th><th>Record</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
"""


def render_row(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "")
    return (
        "      <tr>"
        f"<td>{esc(record.get('run_id'))}</td>"
        f"<td class=\"status-{esc(status)}\">{esc(status)}</td>"
        f"<td>{esc(record.get('pod_id'))}<br>{esc(record.get('pod_name'))}</td>"
        f"<td>{esc(record.get('cost_per_hr'))}</td>"
        f"<td>{esc(record.get('verification_ok'))}</td>"
        f"<td>{esc(record.get('cleanup_status'))}</td>"
        f"<td>{esc(record.get('lock_status'))}</td>"
        f"<td class=\"path\">{esc(record.get('path'))}</td>"
        "</tr>"
    )


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))
