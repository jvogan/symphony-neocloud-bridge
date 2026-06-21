from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .contract import LIVE_OUTPUT_PLACEHOLDER_RE
from .manifest import get_nested, infer_scale
from .util import redact, redact_text

TEXT_ARTIFACT_SUFFIXES = {".csv", ".html", ".json", ".jsonl", ".log", ".md", ".txt", ".tsv", ".xml", ".yaml", ".yml"}
MAX_MARKER_SCAN_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    path: str
    required: bool
    present: bool
    sha256: str | None
    forbidden_markers: list[str]


def build_closeout(manifest: dict[str, Any], base_dir: str | Path = ".") -> dict[str, Any]:
    base = Path(base_dir)
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    monitoring = manifest.get("monitoring", {}) if isinstance(manifest.get("monitoring"), dict) else {}
    closeout_policy = manifest.get("closeout", {}) if isinstance(manifest.get("closeout"), dict) else {}
    # Provider-neutral evidence root. Defaults to runpod-execution so existing manifests are
    # unchanged; a batch provider (e.g. HF Jobs) sets startup.execution_dir to its own directory.
    exec_dir = str(startup.get("execution_dir") or "runpod-execution")
    status_path = resolve_path(base, startup.get("status_file", f"{exec_dir}/status.json"))
    heartbeat_path = resolve_path(base, startup.get("heartbeat_file", f"{exec_dir}/monitor_events.ndjson"))
    log_path = resolve_path(base, startup.get("log_file", f"{exec_dir}/logs/startup.log"))
    hash_path = resolve_path(base, f"{exec_dir}/artifact_hashes.jsonl")
    egress_status_path = base / exec_dir / "egress_status.json"
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}

    artifacts = [artifact_record(base, item) for item in manifest.get("expected_artifacts", []) if isinstance(item, dict)]
    missing_required = [record.path for record in artifacts if record.required and not record.present]
    forbidden_artifacts = [
        {"artifact_id": record.artifact_id, "path": record.path, "markers": record.forbidden_markers}
        for record in artifacts
        if record.forbidden_markers
    ]
    marker_scan_enforced = bool(manifest.get("remote_launch_allowed"))
    status = read_json(status_path)
    heartbeat = read_last_json_line(heartbeat_path)
    egress_status = read_json(egress_status_path)
    egress_ok = egress_closeout_ok(artifact_egress, egress_status)
    missing_required_evidence = required_evidence_gaps(monitoring, closeout_policy, log_path, heartbeat_path, hash_path)
    success = (
        not missing_required
        and not missing_required_evidence
        and status.get("status") in ("succeeded", "success", "completed")
        and (not marker_scan_enforced or not forbidden_artifacts)
        and egress_ok
    )

    return {
        "run_id": manifest.get("run_id"),
        "provider": (manifest.get("provider", {}) or {}).get("name", "runpod") if isinstance(manifest.get("provider", {}), dict) else "runpod",
        "task_scale": get_nested(manifest, ["workload", "scale"], "") or infer_scale(manifest),
        "status": "succeeded" if success else "failed",
        "status_file": str(status_path),
        "log_file": str(log_path),
        "heartbeat_file": str(heartbeat_path),
        "last_heartbeat": heartbeat,
        "workload_status": status,
        "egress_status": egress_status,
        "egress_ok": egress_ok,
        "artifacts": [record.__dict__ for record in artifacts],
        "missing_required_artifacts": missing_required,
        "missing_required_evidence": missing_required_evidence,
        "forbidden_artifact_markers": forbidden_artifacts,
        "forbidden_artifact_markers_enforced": marker_scan_enforced,
        "claim_level": "artifact_execution_only",
    }


def write_closeout_files(manifest: dict[str, Any], base_dir: str | Path = ".") -> dict[str, Any]:
    base = Path(base_dir)
    closeout = build_closeout(manifest, base)
    # Honor the manifest's evidence root (default runpod-execution, so RunPod manifests are
    # unchanged) so a batch provider's closeout files land beside its evidence, not in a stray dir.
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    execution_dir = base / str(startup.get("execution_dir") or "runpod-execution")
    execution_dir.mkdir(parents=True, exist_ok=True)

    # closeout files are the artifacts most likely pasted into Linear/PRs; they ingest a
    # pod-authored egress destination (s3:// etc.) - scrub before writing, return real data.
    (execution_dir / "closeout.json").write_text(json.dumps(redact(closeout), indent=2, sort_keys=True) + "\n")
    (execution_dir / "artifact_hashes.json").write_text(json.dumps(redact(closeout["artifacts"]), indent=2, sort_keys=True) + "\n")
    (execution_dir / "symphony_outcome.md").write_text(redact_text(render_outcome(manifest, closeout)))
    return closeout


def render_outcome(manifest: dict[str, Any], closeout: dict[str, Any]) -> str:
    artifact_lines = "\n".join(
        f"  - {item['artifact_id']}: {item['sha256'] or 'missing'} ({item['path']})"
        for item in closeout["artifacts"]
    ) or "  - none"
    status = closeout["status"]
    provider = closeout.get("provider", "runpod")
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    workload_status = closeout.get("workload_status", {})
    last_heartbeat = closeout.get("last_heartbeat") or {}
    last_heartbeat_ts = last_heartbeat.get("ts", "") if isinstance(last_heartbeat, dict) else ""
    return f"""<!-- symphony-outcome
outcome_version: 1
status: {status}
pack_id: symphony-cloud-bridge
pack_issue_id: {manifest.get("run_id", "")}
provider: {provider}
compute_profile: {manifest.get("compute_profile", "")}
remote_launch: local_closeout
pod_id:
template_id: {runpod.get("templateId", "")}
image: {runpod.get("imageName", "")}
data_center:
runtime_minutes:
estimated_cost_usd:
actual_cost_usd:
cost_source: local_closeout
cost_report:
dashboard_record:
monitoring_summary:
  pod_state: not_polled
  workload_status: {workload_status.get("status", "missing")}
  last_heartbeat: {last_heartbeat_ts}
  live_progress_channel: local_packet
  last_progress_advance:
  productivity_proof: artifact_hashes_and_status
  escalation_tier_reached:
  silence_timeout_result:
egress_status:
  mode: {closeout.get("egress_status", {}).get("mode", "")}
  status: {closeout.get("egress_status", {}).get("status", "")}
  verified: {str(closeout.get("egress_ok", False)).lower()}
artifact_hashes:
{artifact_lines}
validation_summary: {status}
artifact_marker_scan:
  enforced: {str(closeout.get("forbidden_artifact_markers_enforced", False)).lower()}
  findings: {len(closeout.get("forbidden_artifact_markers", []))}
cleanup_status: local_only
cleanup_verified: not_applicable_local_only
claim_level: artifact_execution_only
scientific_caveats:
  - domain repo owns scientific interpretation
suggested_action: review artifacts and cleanup record
-->
"""


def artifact_record(base: Path, artifact: dict[str, Any]) -> ArtifactRecord:
    artifact_id = str(artifact.get("artifact_id", "artifact"))
    path = str(artifact.get("path", ""))
    required = bool(artifact.get("required", False))
    resolved = resolve_path(base, path)
    present = resolved.is_file()
    digest = sha256_file(resolved) if present else None
    forbidden_markers = forbidden_output_markers(resolved) if present else []
    return ArtifactRecord(artifact_id, path, required, present, digest, forbidden_markers)


def egress_closeout_ok(artifact_egress: dict[str, Any], egress_status: dict[str, Any]) -> bool:
    mode = str(artifact_egress.get("mode") or "workspace_archive")
    status = str(egress_status.get("status") or "")
    if mode == "workspace_archive":
        return status in ("", "not_required", "uploaded", "retained")
    if mode == "network_volume":
        return status == "retained"
    if mode == "runpod_network_volume_s3":
        return status in ("downloaded", "verified")
    if mode == "object_store_upload":
        if artifact_egress.get("requires_object_store_upload") is True:
            return status == "verified"
        return status in ("", "not_required", "uploaded", "verified")
    if mode == "aws_s3_presigned_upload":
        if artifact_egress.get("requires_presigned_upload") is True:
            return status == "verified"
        return status in ("", "not_required", "uploaded", "verified")
    if mode == "scp":
        return status in ("", "not_required", "uploaded", "retained")
    if mode == "hf_hub_repo":
        # the run-job flow downloaded and hashed every expected artifact the job pushed to the Hub repo
        return status == "verified"
    return False


def resolve_path(base: Path, path: Any) -> Path:
    return base / packet_relative_path(path)


def packet_relative_path(path: Any) -> Path:
    """Map pod paths such as /workspace/runpod-execution/x into a local packet path."""
    raw = str(path or "")
    candidate = Path(raw)
    parts = list(candidate.parts)
    if parts and parts[0] == "/":
        parts = parts[1:]
    if parts and parts[0] == "workspace":
        parts = parts[1:]
    cleaned = [part for part in parts if part not in ("", ".") and part != ".."]
    if not cleaned:
        return Path(".")
    return Path(*cleaned)


def required_evidence_gaps(
    monitoring: dict[str, Any],
    closeout_policy: dict[str, Any],
    log_path: Path,
    heartbeat_path: Path,
    hash_path: Path,
) -> list[str]:
    gaps: list[str] = []
    if monitoring.get("requires_log_artifact") is True and not log_path.is_file():
        gaps.append(f"missing required log artifact: {log_path}")
    if monitoring.get("requires_workload_heartbeat") is True and not heartbeat_path.is_file():
        gaps.append(f"missing required workload heartbeat: {heartbeat_path}")
    if closeout_policy.get("record_artifact_hashes") is True and not hash_path.is_file():
        gaps.append(f"missing required artifact hash ledger: {hash_path}")
    return gaps


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def forbidden_output_markers(path: Path) -> list[str]:
    if path.suffix.lower() not in TEXT_ARTIFACT_SUFFIXES:
        return []
    try:
        if path.stat().st_size > MAX_MARKER_SCAN_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    markers = {match.group(2).lower() for match in LIVE_OUTPUT_PLACEHOLDER_RE.finditer(text)}
    return sorted(markers)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_last_json_line(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return {}
    if not lines:
        return {}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"raw": lines[-1]}
    return data if isinstance(data, dict) else {"raw": data}
