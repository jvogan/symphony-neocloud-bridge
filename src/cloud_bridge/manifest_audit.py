from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .manifest import ManifestError, load_manifest, validate_manifest


SKIP_DIRS = {
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    "node_modules",
}
MANIFEST_NAME_HINTS = {
    "launch_manifest.json",
    "runpod-launch-manifest.template.json",
    "sidecar-runpod-launch-bundle.json",
}


def audit_manifest_tree(root: str | Path, *, migration_hints: bool = False) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for path in iter_json_files(base):
        candidate = inspect_candidate(path, base, migration_hints=migration_hints)
        if candidate is not None:
            results.append(candidate)
    failures = [item for item in results if not item.get("ok")]
    warnings = [item for item in results if item.get("warnings")]
    return {
        "root": str(base),
        "ok": not failures,
        "summary": {
            "manifest_candidates": len(results),
            "failures": len(failures),
            "with_warnings": len(warnings),
        },
        "issue_summary": summarize_results(results),
        "results": results,
    }


def iter_json_files(base: Path):
    if base.is_file():
        if base.suffix == ".json":
            yield base
        return
    for path in base.rglob("*.json"):
        if any(part in SKIP_DIRS for part in path.relative_to(base).parts):
            continue
        yield path


def inspect_candidate(path: Path, base: Path, *, migration_hints: bool = False) -> dict[str, Any] | None:
    rel = relative_display_path(path, base)
    name_hint = path.name in MANIFEST_NAME_HINTS
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        if name_hint:
            return {"path": rel, "ok": False, "errors": [{"path": "json", "message": str(exc)}], "warnings": []}
        return None
    if not isinstance(raw, dict):
        return None
    if not is_manifest_candidate(raw, name_hint):
        return None
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        result = {"path": rel, "ok": False, "errors": [{"path": "json", "message": str(exc)}], "warnings": []}
        if migration_hints:
            result["migration_hints"] = []
        return result
    validation = validate_manifest(manifest)
    result = {
        "path": rel,
        "ok": validation.ok,
        "errors": [issue.__dict__ for issue in validation.errors],
        "warnings": [issue.__dict__ for issue in validation.warnings],
    }
    if migration_hints:
        result["migration_hints"] = build_migration_hints(raw)
    return result


def build_migration_hints(raw: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if raw.get("manifest_kind") != "symphony_runpod_launch":
        hints.append("add manifest_kind='symphony_runpod_launch' and provider={name:'runpod', adapter:'runpod_pods_v1'}")
    if isinstance(raw.get("stage_contract"), dict) and not isinstance(nested(raw, "workload", "stage_contract"), dict):
        hints.append("move top-level stage_contract under workload.stage_contract")
    if "expected_outputs" in raw and "expected_artifacts" not in raw:
        hints.append("rename expected_outputs to expected_artifacts entries with artifact_id, path, required, and sha256_required")
    if "provider_handoff_policy" in raw:
        hints.append("replace provider_handoff_policy with worker_coordination plus closeout cleanup policy")
    if "fallback_policy" in raw:
        hints.append("move fallback intent into workload.checkpoint_policy, monitoring, and recovery/runbook notes")
    repo = raw.get("repo") if isinstance(raw.get("repo"), dict) else {}
    source = str(repo.get("source") or "")
    if source == "local_snapshot":
        hints.append("local_snapshot is dry-run only for remote launch; use prepared_snapshot/object_store_archive with archive_sha256 or git_remote with immutable ref")
    if source == "git_remote_or_snapshot":
        hints.append("repo.source git_remote_or_snapshot is deprecated; choose git_remote for immutable clones or prepared_snapshot/object_store_archive for private source packets")
    egress = raw.get("artifact_egress") if isinstance(raw.get("artifact_egress"), dict) else {}
    if egress.get("mode") in ("object_store_upload", "aws_s3_presigned_upload"):
        hints.append("required object-store egress needs trusted orchestrator verification recorded as egress_status.status='verified'")
    runpod = raw.get("runpod") if isinstance(raw.get("runpod"), dict) else {}
    if runpod.get("cloudType") == "COMMUNITY":
        hints.append("COMMUNITY launch now requires safety.community_cloud_allowed=true and public/synthetic/sanitized data policy")
    gpu_count = numeric_value(runpod.get("gpuCount"))
    if runpod.get("gpuTypeIds") == [] and gpu_count and gpu_count > 0:
        hints.append("avoid empty gpuTypeIds for GPU smokes; pin a small explicit GPU family, then use gpu-catalog --manifest before retrying no-instances failures")
    if gpu_count and gpu_count > 0 and runpod.get("gpuTypeIds") and runpod.get("dataCenterIds"):
        hints.append("run gpu-catalog --manifest before paid GPU retries so wrong-DC catalog mismatch is separated from capacity-zero")
    if not isinstance(nested(raw, "startup", "progress"), dict):
        hints.append("add startup.progress.http_status_server_port for live progress when running nontrivial remote workloads")
    if not isinstance(nested(raw, "startup", "terminal_hold"), dict):
        hints.append("add startup.terminal_hold so crashes or quick success preserve forensic/artifact fetch time")
    inspection_port = nested(raw, "startup", "inspection", "http_artifact_server_port")
    access = raw.get("access") if isinstance(raw.get("access"), dict) else {}
    if inspection_port and access.get("http_proxy_required") is not True and access.get("tcp_ports_required") is not True:
        hints.append("inspection HTTP artifact server should declare access.http_proxy_required or access.tcp_ports_required so agents know which path to verify")
    return hints


def summarize_results(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "errors": summarize_issues(results, "errors"),
        "warnings": summarize_issues(results, "warnings"),
        "migration_hints": summarize_hints(results),
    }


def summarize_issues(results: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        for issue in item.get(field, []):
            if not isinstance(issue, dict):
                continue
            path = str(issue.get("path") or "<unknown>")
            message = str(issue.get("message") or "")
            key = (path, message)
            bucket = buckets.setdefault(key, {"path": path, "message": message, "count": 0, "examples": []})
            bucket["count"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(item.get("path"))
    return sorted(buckets.values(), key=lambda item: (-int(item["count"]), str(item["path"]), str(item["message"])))


def summarize_hints(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for item in results:
        for hint in item.get("migration_hints", []):
            hint_text = str(hint)
            bucket = buckets.setdefault(hint_text, {"hint": hint_text, "count": 0, "examples": []})
            bucket["count"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(item.get("path"))
    return sorted(buckets.values(), key=lambda item: (-int(item["count"]), str(item["hint"])))


def is_manifest_candidate(raw: dict[str, Any], name_hint: bool) -> bool:
    if name_hint:
        return True
    if raw.get("handoff_kind"):
        return False
    if raw.get("request_kind"):
        return False
    if raw.get("manifest_kind") == "symphony_runpod_launch":
        return True
    if "remote_launch_allowed" in raw:
        return True
    if "runpod" in raw and ("startup" in raw or "artifact_egress" in raw):
        return True
    return False


def nested(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def relative_display_path(path: Path, base: Path) -> str:
    if base.is_file():
        return path.name
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
