"""Hugging Face Jobs execution flow - the batch-job analog of RunPod's run_remote_flow.

A HF Job is a one-shot container, so the lifecycle is submit -> poll -> verify, not
rent -> attach -> tear down. The job runs the workload and persists its own outputs to a Hub
repo (a read-write bucket volume, or a self-push using its HF token); after the job reaches a
terminal stage this flow downloads those artifacts, hashes them, and writes the same evidence
files (status.json, logs, artifact_hashes.jsonl, egress_status.json) that closeout consumes.
That evidence contract - not run_remote_flow - is the seam a second provider plugs into.

Safety: jobs auto-terminate (and stop billing) on exit/error/timeout, so there is no
bill-forever risk like an orphaned pod. The remaining footgun is HF's 30-minute default
timeout, so a timeoutSeconds is always sent and an over-budget poll cancels the job.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from cloud_bridge.manifest import get_nested
from cloud_bridge.util import now, redact
from .rest import (
    HfJobsClient,
    HfJobsError,
    SUCCESS_STAGE,
    is_terminal_stage,
    job_stage,
    resolve_url,
)


# Indicative per-minute USD prices for the spend guard's dry-run estimate. The live catalog
# (GET /api/jobs/hardware) is authoritative and used at execute time; this table only has to be
# close enough to refuse an obviously over-budget flavor offline. Verify against the live catalog.
STATIC_FLAVOR_USD_PER_MIN: dict[str, float] = {
    "cpu-basic": 0.0002,
    "cpu-upgrade": 0.0005,
    "cpu-xl": 0.0167,
    "cpu-performance": 0.0317,
    "t4-small": 0.0067,
    "t4-medium": 0.0100,
    "l4x1": 0.0133,
    "l4x4": 0.0633,
    "l40sx1": 0.0300,
    "l40sx4": 0.1383,
    "l40sx8": 0.3917,
    "a10g-small": 0.0167,
    "a10g-large": 0.0250,
    "a10g-largex2": 0.0500,
    "a10g-largex4": 0.0833,
    "a100-large": 0.0417,
    "a100x4": 0.1667,
    "a100x8": 0.3333,
    "h200": 0.0833,
}
DEFAULT_FLAVOR = "cpu-basic"
DEFAULT_TIMEOUT_SECONDS = 1800


def hf_block(manifest: dict[str, Any]) -> dict[str, Any]:
    block = manifest.get("huggingface")
    return block if isinstance(block, dict) else {}


def egress_block(manifest: dict[str, Any]) -> dict[str, Any]:
    block = manifest.get("artifact_egress")
    return block if isinstance(block, dict) else {}


def execution_dir(manifest: dict[str, Any]) -> str:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    return str(startup.get("execution_dir") or "runpod-execution")


def derived_command(manifest: dict[str, Any]) -> list[str]:
    """Use an explicit huggingface.command, else wrap the startup commands in a bash script."""
    hf = hf_block(manifest)
    command = hf.get("command")
    if isinstance(command, list) and command:
        return [str(part) for part in command]
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    lines = [str(line) for line in startup.get("commands", []) if isinstance(line, str)]
    if lines:
        return ["bash", "-lc", "\n".join(lines)]
    return []


def build_job_spec(manifest: dict[str, Any], *, secrets: dict[str, str] | None = None) -> dict[str, Any]:
    """Build the POST /api/jobs/{ns} body. camelCase wire keys, image XOR space.

    Secret values are injected only when ``secrets`` is provided (execute path). They go in the
    encrypted ``secrets`` field, never ``environment`` (which HF stores/echoes in plaintext).
    """
    hf = hf_block(manifest)
    spec: dict[str, Any] = {
        "command": derived_command(manifest),
        "arguments": [str(arg) for arg in hf.get("arguments", []) if isinstance(arg, (str, int, float))],
        "environment": {str(k): str(v) for k, v in (hf.get("environment") or {}).items()},
        "flavor": str(hf.get("flavor") or DEFAULT_FLAVOR),
        "timeoutSeconds": int(hf.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS),
    }
    space_id = str(hf.get("spaceId") or "")
    if space_id:
        spec["spaceId"] = space_id
    else:
        spec["dockerImage"] = str(hf.get("dockerImage") or "python:3.12-slim")
    labels = hf.get("labels")
    if isinstance(labels, dict) and labels:
        spec["labels"] = {str(k): str(v) for k, v in labels.items()}
    volumes = hf.get("volumes")
    if isinstance(volumes, list) and volumes:
        spec["volumes"] = volumes
    if secrets:
        spec["secrets"] = dict(secrets)
    return spec


def secret_refs(manifest: dict[str, Any]) -> dict[str, str]:
    """Declared {job_secret_name: local_env_var} mappings the job needs (e.g. its push token)."""
    hf = hf_block(manifest)
    refs = hf.get("secret_refs")
    if not isinstance(refs, dict):
        return {}
    return {str(name): str(env_var) for name, env_var in refs.items() if env_var}


def resolve_secrets(manifest: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Resolve declared secret refs from the environment. Returns (resolved, missing_env_vars)."""
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for job_name, env_var in secret_refs(manifest).items():
        value = os.environ.get(env_var, "")
        if value:
            resolved[job_name] = value
        else:
            missing.append(env_var)
    return resolved, missing


def safe_spec_for_record(spec: dict[str, Any]) -> dict[str, Any]:
    """Persist the spec safely for the audit record. Value-aware redaction scrubs every non-secret
    field (catching e.g. a token wrongly placed in plaintext environment); the secrets field is
    reduced to names only so the record shows which secrets were sent without leaking their values."""
    safe = redact({key: value for key, value in spec.items() if key != "secrets"})
    if isinstance(spec.get("secrets"), dict):
        safe["secrets"] = {name: "<redacted>" for name in spec["secrets"]}
    return safe


def live_flavor_usd_per_min(hardware: list[dict[str, Any]], flavor: str) -> float | None:
    for item in hardware:
        ident = str(item.get("id") or item.get("flavor") or item.get("name") or "")
        if ident != flavor:
            continue
        label = str(item.get("unit_label") or "").lower()
        if not label.startswith("min"):
            continue
        micro = item.get("unit_cost_micro_usd")
        if isinstance(micro, (int, float)):
            return float(micro) / 1_000_000.0
        usd = item.get("unit_cost_usd")
        if isinstance(usd, (int, float)):
            return float(usd)
    return None


def estimate_worst_case_cost(
    flavor: str,
    timeout_seconds: int,
    *,
    hardware: list[dict[str, Any]] | None = None,
) -> float | None:
    """Worst-case USD = per-minute price x full timeout. None if the flavor price is unknown."""
    per_min: float | None = None
    if hardware:
        per_min = live_flavor_usd_per_min(hardware, flavor)
    if per_min is None:
        per_min = STATIC_FLAVOR_USD_PER_MIN.get(flavor)
    if per_min is None:
        return None
    minutes = max(int(timeout_seconds), 0) / 60.0
    return round(per_min * minutes, 6)


def job_spec_blockers(manifest: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not spec.get("command"):
        blockers.append("huggingface.command or startup.commands must define the job command")
    # build_job_spec always resolves an image (defaulting to python:3.12-slim, as the manifest
    # validator documents), so there is no missing-image failure mode to block on here.
    egress = egress_block(manifest)
    if egress.get("mode") == "hf_hub_repo" and not egress.get("repo_id"):
        blockers.append("artifact_egress.repo_id is required for hf_hub_repo egress")
    return blockers


def sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def egress_artifact_plan(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Map each expected artifact to (repo_path to download, local_path closeout checks)."""
    egress = egress_block(manifest)
    path_map = egress.get("path_map") if isinstance(egress.get("path_map"), dict) else {}
    plan: list[dict[str, str]] = []
    for item in manifest.get("expected_artifacts", []):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        local_path = str(item["path"])
        artifact_id = str(item.get("artifact_id") or local_path)
        repo_path = str(path_map.get(artifact_id) or local_path)
        plan.append({"artifact_id": artifact_id, "repo_path": repo_path, "local_path": local_path})
    return plan


def verify_pushed_artifacts(
    manifest: dict[str, Any],
    client: HfJobsClient,
    *,
    base_dir: Path,
) -> dict[str, Any]:
    """Download each expected artifact the job pushed to its Hub repo, hash it, and write the
    egress_status.json + artifact_hashes.jsonl evidence closeout reads."""
    egress = egress_block(manifest)
    repo_id = str(egress.get("repo_id") or "")
    repo_type = str(egress.get("repo_type") or "dataset")
    revision = str(egress.get("revision") or "main")
    plan = egress_artifact_plan(manifest)
    results: list[dict[str, Any]] = []
    hash_lines: list[str] = []
    all_ok = True
    for entry in plan:
        url = resolve_url(client.base_url, repo_type, repo_id, entry["repo_path"], revision=revision)
        local_path = base_dir / entry["local_path"]
        try:
            data = client.download(url)
        except HfJobsError as exc:
            all_ok = False
            results.append({**entry, "ok": False, "error": str(exc)})
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        digest = sha256_bytes(data)
        results.append({**entry, "ok": True, "bytes": len(data), "sha256": digest})
        hash_lines.append(json.dumps({"path": entry["local_path"], "sha256": digest}, sort_keys=True))

    exec_dir = base_dir / execution_dir(manifest)
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "artifact_hashes.jsonl").write_text("\n".join(hash_lines) + ("\n" if hash_lines else ""))
    verified = all_ok and bool(plan)
    egress_status = {
        "mode": "hf_hub_repo",
        # canonical string field closeout.egress_closeout_ok reads; mirrors the other egress modes
        "status": "verified" if verified else "incomplete",
        "repo_id": repo_id,
        "repo_type": repo_type,
        "revision": revision,
        "verified": verified,
        "artifacts": results,
    }
    (exec_dir / "egress_status.json").write_text(json.dumps(redact(egress_status), indent=2, sort_keys=True) + "\n")
    return egress_status


def write_evidence_logs(manifest: dict[str, Any], base_dir: Path, log_lines: list[str]) -> str:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    log_rel = str(startup.get("log_file") or f"{execution_dir(manifest)}/logs/job.log")
    log_path = base_dir / log_rel
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(log_lines) + ("\n" if log_lines else ""))
    return log_rel


def write_status_file(manifest: dict[str, Any], base_dir: Path, *, stage: str, success: bool) -> str:
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    status_rel = str(startup.get("status_file") or f"{execution_dir(manifest)}/status.json")
    status_path = base_dir / status_rel
    status_path.parent.mkdir(parents=True, exist_ok=True)
    # "succeeded" only when the job container itself reported COMPLETED. closeout re-gates this
    # against artifact presence + egress before it will call the whole run succeeded.
    payload = {
        "status": "succeeded" if success else "failed",
        "provider": "huggingface",
        "job_stage": stage,
        "claim_basis": "hf_job_terminal_stage",
    }
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return status_rel


def write_record(output: Path, record: dict[str, Any]) -> None:
    (output / "hf_job_record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def poll_until_terminal(
    client: HfJobsClient,
    job_id: str,
    *,
    timeout_seconds: int,
    interval_seconds: int,
) -> tuple[dict[str, Any], str, bool]:
    """Poll job state until terminal or our local poll budget runs out. Returns (job, stage, timed_out)."""
    deadline = time.monotonic() + max(timeout_seconds, 0)
    job = client.get_job(job_id)
    stage = job_stage(job)
    while not is_terminal_stage(stage):
        if time.monotonic() >= deadline:
            return job, stage, True
        time.sleep(max(interval_seconds, 1))
        job = client.get_job(job_id)
        stage = job_stage(job)
    return job, stage, False


def run_job_flow(
    manifest: dict[str, Any],
    *,
    out_dir: str | Path,
    execute: bool,
    max_spend_usd: float | None = None,
    poll_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = 10,
    log_tail: int = 200,
    client: HfJobsClient | None = None,
) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    spec = build_job_spec(manifest)
    flavor = str(spec.get("flavor") or DEFAULT_FLAVOR)
    timeout_seconds = int(spec.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
    estimate = estimate_worst_case_cost(flavor, timeout_seconds)
    record: dict[str, Any] = {
        "ts": now(),
        "action": "run_job",
        "provider": "huggingface",
        "execute": execute,
        "manifest_run_id": manifest.get("run_id"),
        "request": safe_spec_for_record(spec),
        "cost_estimate": {
            "flavor": flavor,
            "timeout_seconds": timeout_seconds,
            "worst_case_usd": estimate,
            "basis": "indicative_static_table",
            "max_spend_usd": max_spend_usd,
        },
        "submit": {},
        "poll": {},
        "egress": {},
        "status": "started",
    }
    write_record(output, record)

    blockers = job_spec_blockers(manifest, spec)
    if blockers:
        record["status"] = "blocked_request"
        record["blockers"] = blockers
        write_record(output, record)
        return record

    if max_spend_usd is not None and (estimate is None or estimate > max_spend_usd):
        reason = (
            f"worst-case estimate {estimate} exceeds --max-spend-usd {max_spend_usd}"
            if estimate is not None
            else f"flavor {flavor!r} has no known price; cannot prove it is under --max-spend-usd {max_spend_usd}"
        )
        record["status"] = "blocked_spend_ceiling"
        record["blockers"] = [reason]
        write_record(output, record)
        return record

    if not execute:
        record["status"] = "dry_run_request"
        write_record(output, record)
        return record

    secrets, missing = resolve_secrets(manifest)
    if missing:
        record["status"] = "blocked_missing_secret"
        record["blockers"] = [f"declared secret env vars are unset: {', '.join(sorted(missing))}"]
        write_record(output, record)
        return record

    hf = hf_block(manifest)
    api = client or HfJobsClient(namespace=str(hf.get("namespace") or "") or None)
    # Refine the estimate against live pricing where possible before spending.
    try:
        hardware = api.hardware()
        live = estimate_worst_case_cost(flavor, timeout_seconds, hardware=hardware)
        if live is not None:
            record["cost_estimate"].update({"worst_case_usd": live, "basis": "live_catalog"})
            if max_spend_usd is not None and live > max_spend_usd:
                record["status"] = "blocked_spend_ceiling"
                record["blockers"] = [f"live worst-case estimate {live} exceeds --max-spend-usd {max_spend_usd}"]
                write_record(output, record)
                return record
    except HfJobsError:
        record["cost_estimate"]["live_lookup"] = "failed; used indicative table"

    exec_spec = build_job_spec(manifest, secrets=secrets)
    job_id = ""
    last_stage = ""
    try:
        submitted = api.submit_job(exec_spec)
        job_id = str(submitted.get("id") or "")
        last_stage = job_stage(submitted)
        record["submit"] = {"job_id": job_id, "stage": last_stage}
        write_record(output, record)
        if not job_id:
            record["status"] = "submit_no_job_id"
            write_record(output, record)
            return record

        job, stage, timed_out = poll_until_terminal(
            api, job_id, timeout_seconds=poll_timeout_seconds, interval_seconds=poll_interval_seconds
        )
        last_stage = stage
        record["poll"] = {"final_stage": stage, "timed_out": timed_out, "durations": redact(job.get("durations") or {})}

        log_lines = api.fetch_logs(job_id, tail=log_tail)
        record["log_file"] = write_evidence_logs(manifest, output, log_lines)

        success = (stage == SUCCESS_STAGE) and not timed_out
        record["status_file"] = write_status_file(manifest, output, stage=stage, success=success)

        if timed_out:
            record["status"] = "poll_timeout"
        elif stage != SUCCESS_STAGE:
            record["status"] = f"job_{stage.lower()}" if stage else "job_unknown_stage"
        else:
            egress = verify_pushed_artifacts(manifest, api, base_dir=output)
            record["egress"] = redact(egress)
            record["status"] = "artifacts_verified" if egress.get("status") == "verified" else "egress_failed"
        write_record(output, record)
        return record
    except HfJobsError as exc:
        record["error"] = str(exc)
        record["status"] = "error"
        write_record(output, record)
        return record
    finally:
        # Stop billing if we are leaving while the job may still be running (poll timeout, crash).
        if execute and job_id and not is_terminal_stage(last_stage):
            try:
                cancel = api.cancel_job(job_id)
                record["cancel"] = {"requested": True, "stage": job_stage(cancel) or last_stage}
            except HfJobsError as exc:
                record["cancel"] = {"requested": True, "error": str(exc)}
            write_record(output, record)
