from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cost import cost_report_from_record, unwrap_remote_record
from .util import redact, redact_text


def write_remote_outcome(
    record_path: str | Path,
    out_path: str | Path,
    *,
    fetch_billing: bool = False,
) -> dict[str, Any]:
    record_file = Path(record_path).resolve()
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(record_file.read_text())
    if is_hf_job_record(record):
        # HF Jobs is a batch lifecycle with its own record shape (no pod/cleanup/RunPod billing),
        # so it gets its own renderer rather than being forced through the pod-shaped path below.
        return write_hf_job_outcome(record, record_file, output)
    remote = unwrap_remote_record(record)
    create_record = load_linked_create_record(remote)
    progress_record = load_progress_record(record_file)
    cost = cost_report_from_record(record_file, fetch_billing=fetch_billing)
    text = render_remote_outcome(remote, cost, record_file, create_record=create_record, progress_record=progress_record)
    output.write_text(text)
    payload = {
        "record_path": str(record_file),
        "outcome_path": str(output),
        "status": remote.get("status"),
        "cleanup_status": (remote.get("cleanup") or {}).get("status") if isinstance(remote.get("cleanup"), dict) else "",
        "cost_source": cost.get("cost_source"),
    }
    (output.with_suffix(".json")).write_text(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")
    return payload


def load_linked_create_record(remote: dict[str, Any]) -> dict[str, Any]:
    create = remote.get("create", {}) if isinstance(remote.get("create"), dict) else {}
    record_path = str(create.get("record_path") or "")
    if not record_path:
        return {}
    try:
        data = json.loads(Path(record_path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_progress_record(record_file: Path) -> dict[str, Any]:
    progress_path = record_file.parent / "remote_progress_latest.json"
    try:
        data = json.loads(progress_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def render_remote_outcome(
    remote: dict[str, Any],
    cost: dict[str, Any],
    record_file: Path,
    *,
    create_record: dict[str, Any] | None = None,
    progress_record: dict[str, Any] | None = None,
) -> str:
    create_record = create_record or {}
    progress_record = progress_record or {}
    create = remote.get("create", {}) if isinstance(remote.get("create"), dict) else {}
    pod = create.get("pod", {}) if isinstance(create.get("pod"), dict) else {}
    request = create_record.get("request", {}) if isinstance(create_record.get("request"), dict) else {}
    response = create_record.get("response", {}) if isinstance(create_record.get("response"), dict) else {}
    preview = create_record.get("preview", {}) if isinstance(create_record.get("preview"), dict) else {}
    verification = remote.get("verification", {}) if isinstance(remote.get("verification"), dict) else {}
    closeout = verification.get("closeout", {}) if isinstance(verification.get("closeout"), dict) else {}
    cleanup = remote.get("cleanup", {}) if isinstance(remote.get("cleanup"), dict) else {}
    classification = progress_record.get("classification", {}) if isinstance(progress_record.get("classification"), dict) else {}
    artifacts = closeout.get("artifacts", []) if isinstance(closeout.get("artifacts"), list) else []
    artifact_lines = "\n".join(
        f"  - {item.get('artifact_id')}: {item.get('sha256') or 'missing'} ({item.get('path')})"
        for item in artifacts
        if isinstance(item, dict)
    ) or "  - none"
    missing_artifact_lines = outcome_list(closeout.get("missing_required_artifacts"))
    missing_evidence_lines = outcome_list(closeout.get("missing_required_evidence"))
    marker_findings = closeout.get("forbidden_artifact_markers", [])
    marker_count = len(marker_findings) if isinstance(marker_findings, list) else 0
    egress_verified = outcome_bool(closeout.get("egress_ok", ""))
    verifier_error = first_present(verification.get("error"), verification.get("error_type"))
    cleanup_verified = str(cleanup.get("status") in ("verified", "already_absent")).lower()
    billing = cost.get("billing", {}) if isinstance(cost.get("billing"), dict) else {}
    estimate = cost.get("estimate", {}) if isinstance(cost.get("estimate"), dict) else {}
    run_id = str(remote.get("manifest_run_id") or cost.get("run_id") or "")
    compute_profile = first_present(nested(preview, "plan", "compute", "profile"), nested(preview, "plan", "compute_profile"))
    template_id = first_present(request.get("templateId"), response.get("templateId"), pod.get("templateId"))
    image = first_present(request.get("imageName"), response.get("imageName"), pod.get("imageName"))
    data_center = first_present(
        response.get("dataCenterId"),
        nested(response, "machine", "dataCenterId"),
        nested(response, "machine", "dataCenter", "id"),
        nested(pod, "machine", "dataCenterId"),
    )
    return f"""<!-- symphony-outcome
outcome_version: 1
status: {remote.get("status", "")}
pack_id: symphony-cloud-bridge
pack_issue_id: {run_id}
provider: runpod
compute_profile: {compute_profile}
remote_launch: remote_run
pod_id: {create.get("pod_id") or pod.get("id") or ""}
template_id: {template_id}
image: {image}
data_center: {data_center}
runtime_minutes: {round(float(estimate.get("runtime_seconds") or 0) / 60.0, 3)}
estimated_cost_usd: {estimate.get("amount_usd", "")}
actual_cost_usd: {billing.get("amount_usd") if billing.get("amount_usd") is not None else ""}
cost_source: {cost.get("cost_source", "")}
cost_report: {record_file}
dashboard_record: {record_file}
monitoring_summary:
  pod_state: {pod.get("desiredStatus", "")}
  workload_status: {(verification.get("status") or {}).get("status", "") if isinstance(verification.get("status"), dict) else ""}
  progress_report_classification_state: {classification.get("state", "")}
  progress_report_workload_progressing: {outcome_bool(classification.get("workload_progressing", ""))}
  progress_report_monitor_alive: {outcome_bool(classification.get("monitor_alive", ""))}
  progress_report_outage_suspected: {outcome_bool(classification.get("outage_suspected", ""))}
  progress_report_next_action: {classification.get("next_action", "")}
  last_heartbeat:
  live_progress_channel:
  last_progress_advance:
  productivity_proof: artifact_packet_and_closeout
  escalation_tier_reached:
  silence_timeout_result:
egress_status:
  mode: {(closeout.get("egress_status") or {}).get("mode", "") if isinstance(closeout.get("egress_status"), dict) else ""}
  status: {(closeout.get("egress_status") or {}).get("status", "") if isinstance(closeout.get("egress_status"), dict) else ""}
  verified: {egress_verified}
verification:
  mode: {verification.get("mode", "")}
  ok: {outcome_bool(verification.get("ok", ""))}
  error: {verifier_error}
artifact_hashes:
{artifact_lines}
missing_required_artifacts:
{missing_artifact_lines}
missing_required_evidence:
{missing_evidence_lines}
validation_summary: {closeout.get("status", "")}
artifact_marker_scan:
  enforced: {outcome_bool(closeout.get("forbidden_artifact_markers_enforced", ""))}
  findings: {marker_count}
cleanup_status: {cleanup.get("status", "")}
cleanup_verified: {cleanup_verified}
claim_level: artifact_execution_only
scientific_caveats:
  - domain repo owns scientific interpretation
suggested_action: review artifacts, billing, and cleanup verification
-->
"""


def nested(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current if current not in (None, {}, []) else ""


def first_present(*values: Any) -> str:
    for value in values:
        if value not in (None, "", {}, []):
            return str(value)
    return ""


def outcome_bool(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def outcome_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "  - none"
    return "\n".join(f"  - {item}" for item in value)


# --- Hugging Face Jobs outcome (batch-provider record shape) ----------------


def is_hf_job_record(record: dict[str, Any]) -> bool:
    return isinstance(record, dict) and (record.get("provider") == "huggingface" or record.get("action") == "run_job")


def load_hf_closeout(record: dict[str, Any], record_file: Path) -> dict[str, Any]:
    """Load the sibling closeout.json (written by `closeout`) for the authoritative final gate."""
    status_file = str(record.get("status_file") or "")
    exec_dir = Path(status_file).parts[0] if status_file else "hf-execution"
    try:
        data = json.loads((record_file.parent / exec_dir / "closeout.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_hf_job_outcome(record: dict[str, Any], record_file: Path, output: Path) -> dict[str, Any]:
    closeout = load_hf_closeout(record, record_file)
    output.write_text(redact_text(render_hf_job_outcome(record, closeout, record_file)))
    cancel = record.get("cancel", {}) if isinstance(record.get("cancel"), dict) else {}
    payload = {
        "record_path": str(record_file),
        "outcome_path": str(output),
        "status": str(closeout.get("status") or record.get("status") or ""),
        "job_id": (record.get("submit") or {}).get("job_id", "") if isinstance(record.get("submit"), dict) else "",
        "cleanup_status": "canceled" if cancel.get("requested") else "auto_terminated",
        "cost_source": (record.get("cost_estimate") or {}).get("basis", "") if isinstance(record.get("cost_estimate"), dict) else "",
    }
    output.with_suffix(".json").write_text(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")
    return payload


def render_hf_job_outcome(record: dict[str, Any], closeout: dict[str, Any], record_file: Path) -> str:
    closeout = closeout or {}
    submit = record.get("submit", {}) if isinstance(record.get("submit"), dict) else {}
    poll = record.get("poll", {}) if isinstance(record.get("poll"), dict) else {}
    durations = poll.get("durations", {}) if isinstance(poll.get("durations"), dict) else {}
    cost_estimate = record.get("cost_estimate", {}) if isinstance(record.get("cost_estimate"), dict) else {}
    request = record.get("request", {}) if isinstance(record.get("request"), dict) else {}
    egress = record.get("egress", {}) if isinstance(record.get("egress"), dict) else {}
    cancel = record.get("cancel", {}) if isinstance(record.get("cancel"), dict) else {}

    flavor = str(cost_estimate.get("flavor") or request.get("flavor") or "")
    running_secs = float(durations.get("runningSecs") or 0)
    # HF bills only running time; compute the actual cost from running seconds x the flavor price.
    actual_cost = ""
    if flavor and running_secs:
        from .providers.huggingface.jobs import estimate_worst_case_cost

        billed = estimate_worst_case_cost(flavor, int(round(running_secs)))
        actual_cost = "" if billed is None else billed

    # the standalone closeout is the authoritative gate; without it, report the run record's status
    status = str(closeout.get("status") or record.get("status") or "")
    egress_status = closeout.get("egress_status") if isinstance(closeout.get("egress_status"), dict) else egress
    egress_status = egress_status if isinstance(egress_status, dict) else {}
    artifacts = closeout.get("artifacts") if isinstance(closeout.get("artifacts"), list) else egress.get("artifacts", [])
    artifact_lines = "\n".join(
        f"  - {item.get('artifact_id') or item.get('repo_path')}: {item.get('sha256') or 'missing'} "
        f"({item.get('path') or item.get('repo_path')})"
        for item in (artifacts or [])
        if isinstance(item, dict)
    ) or "  - none"
    job_stage = str(poll.get("final_stage") or submit.get("stage") or "")
    egress_verified = closeout.get("egress_ok") if "egress_ok" in closeout else egress.get("verified", "")
    return f"""<!-- symphony-outcome
outcome_version: 1
status: {status}
pack_id: symphony-cloud-bridge
pack_issue_id: {record.get("manifest_run_id", "")}
provider: huggingface
compute_profile: {first_present(closeout.get("compute_profile"))}
remote_launch: run_job
job_id: {submit.get("job_id", "")}
flavor: {flavor}
job_stage: {job_stage}
timed_out: {outcome_bool(poll.get("timed_out", ""))}
pod_id:
template_id:
image: {request.get("dockerImage") or request.get("spaceId") or ""}
data_center:
runtime_minutes: {round(running_secs / 60.0, 4)}
estimated_cost_usd: {cost_estimate.get("worst_case_usd", "")}
actual_cost_usd: {actual_cost}
cost_source: {cost_estimate.get("basis", "")}
cost_report: {record_file}
dashboard_record: {record_file}
monitoring_summary:
  pod_state: not_applicable_batch_job
  workload_status: {job_stage}
  last_heartbeat:
  live_progress_channel: hf_job_logs
  last_progress_advance:
  productivity_proof: artifact_packet_and_closeout
  escalation_tier_reached:
  silence_timeout_result:
egress_status:
  mode: {egress_status.get("mode", "")}
  status: {egress_status.get("status", "")}
  verified: {outcome_bool(egress_verified)}
artifact_hashes:
{artifact_lines}
missing_required_artifacts:
{outcome_list(closeout.get("missing_required_artifacts"))}
missing_required_evidence:
{outcome_list(closeout.get("missing_required_evidence"))}
validation_summary: {status}
cleanup_status: {"canceled" if cancel.get("requested") else "auto_terminated"}
cleanup_verified: {outcome_bool(not cancel.get("error"))}
claim_level: artifact_execution_only
scientific_caveats:
  - domain repo owns scientific interpretation
suggested_action: review artifacts, egress, and HF job billing
-->
"""
