from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .runpod_rest import RunpodRestClient, redact


def cost_report_from_record(
    record_path: str | Path,
    *,
    fetch_billing: bool = False,
    client: RunpodRestClient | None = None,
) -> dict[str, Any]:
    path = Path(record_path).resolve()
    record = json.loads(path.read_text())
    remote = unwrap_remote_record(record)
    pod_id = str(remote.get("create", {}).get("pod_id") or "")
    pod = remote.get("create", {}).get("pod", {}) if isinstance(remote.get("create"), dict) else {}
    start = parse_time(str(pod.get("lastStartedAt") or remote.get("ts") or record.get("ts") or ""))
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    end = parse_time(str(cleanup.get("ts") or record.get("ts") or ""))
    if end is None or (start is not None and end < start):
        end = datetime.now(timezone.utc)
    estimate = estimate_cost(pod, start, end)
    billing_records: list[dict[str, Any]] = []
    billing_total = None
    if fetch_billing and pod_id and start:
        api = client or RunpodRestClient()
        query = {
            "podId": pod_id,
            "grouping": "podId",
            "bucketSize": "hour",
            "startTime": format_time(start),
            "endTime": format_time(end),
        }
        billing_records = api.billing_pods(**query)
        if billing_records:
            billing_total = round(sum(float_or_zero(item.get("amount")) for item in billing_records), 6)
    return redact(
        {
            "record_path": str(path),
            "run_id": remote.get("manifest_run_id") or record.get("run_id"),
            "pod_id": pod_id,
            "status": remote.get("status") or record.get("status"),
            "start_time": format_time(start) if start else "",
            "end_time": format_time(end) if end else "",
            "estimate": estimate,
            "billing": {
                "fetched": fetch_billing,
                "amount_usd": billing_total,
                "records": billing_records,
                "source": "runpod_rest_billing_pods" if billing_records else "unavailable",
            },
            "cost_source": "billing_api" if billing_total is not None else "runtime_x_cost_fields",
        }
    )


def unwrap_remote_record(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("action") == "run_handoff" and isinstance(record.get("remote_run"), dict):
        return record["remote_run"]
    return record


def estimate_cost(pod: dict[str, Any], start: datetime | None, end: datetime | None) -> dict[str, Any]:
    cost_per_hr = float_or_none(pod.get("adjustedCostPerHr"))
    if cost_per_hr is None:
        cost_per_hr = float_or_none(pod.get("costPerHr"))
    seconds = (end - start).total_seconds() if start and end else 0.0
    amount = (cost_per_hr or 0.0) * max(seconds, 0.0) / 3600.0
    return {
        "cost_per_hr": cost_per_hr,
        "runtime_seconds": round(max(seconds, 0.0), 3),
        "amount_usd": round(amount, 6),
    }


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    candidates = [value, value.replace(" UTC", "+00:00")]
    if " " in value:
        candidates.append(value.replace(" ", "T", 1).replace(" +0000 UTC", "+00:00"))
    for candidate in candidates:
        normalized = candidate.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def float_or_zero(value: Any) -> float:
    result = float_or_none(value)
    return result if result is not None else 0.0
