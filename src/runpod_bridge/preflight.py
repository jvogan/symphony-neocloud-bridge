from __future__ import annotations

from typing import Any

from .bootstrap_requirements import bootstrap_requirements_report
from .contract import contract_self_check
from .egress import build_egress_plan
from .manifest import build_plan, validate_manifest
from .payload import create_request_payload_report
from .profiles import recommend_profile
from .productivity import build_productivity_plan
from .providers import provider_capabilities
from .runpod_rest import build_create_pod_request
from .source_check import check_source_reachability


def analyze_preflight(manifest: dict[str, Any]) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    plan = build_plan(manifest, validation)
    contract = contract_self_check(manifest)
    egress = build_egress_plan(manifest)
    profile = recommend_profile(manifest)
    provider = provider_capabilities(str(plan.get("provider") or "runpod"))
    source = check_source_reachability(manifest, execute=False)
    payload = create_request_payload_report(build_create_pod_request(manifest)) if validation.ok else {}
    bootstrap = bootstrap_requirements_report(manifest)
    productivity = build_productivity_plan(manifest)
    errors = [issue.__dict__ for issue in validation.errors]
    warnings = [issue.__dict__ for issue in validation.warnings]
    recommendations: list[str] = []

    if plan["task_scale"] in ("large", "huge") and not egress["durable"]:
        recommendations.append("large/huge workloads should use network_volume, scp, object_store_upload, or aws_s3_presigned_upload egress")
    if plan["task_scale"] in ("large", "huge") and not manifest.get("workload", {}).get("checkpoint_policy"):
        recommendations.append("large/huge workloads should declare checkpoint_policy")
    if egress["blockers"]:
        errors.extend({"severity": "error", "path": "artifact_egress", "message": item} for item in egress["blockers"])
    if egress["warnings"]:
        warnings.extend({"severity": "warning", "path": "artifact_egress", "message": item} for item in egress["warnings"])
    errors.extend(contract["errors"])
    warnings.extend(contract["warnings"])
    recommendations.extend(contract["recommendations"])
    if source.get("status") == "not_executed":
        recommendations.append("run source-check --execute before paid launch to prove repo.commit_or_snapshot is reachable")
    if source.get("errors"):
        errors.extend({"severity": "error", "path": "repo", "message": item} for item in source["errors"])
    if payload.get("errors"):
        errors.extend(payload["errors"])
    if payload.get("warnings"):
        warnings.extend(payload["warnings"])
    recommendations.extend(payload.get("recommendations", []))
    if bootstrap.get("errors"):
        errors.extend(bootstrap["errors"])
    if bootstrap.get("warnings"):
        warnings.extend(bootstrap["warnings"])
    recommendations.extend(bootstrap.get("recommendations", []))
    if productivity.get("blockers"):
        warnings.extend(
            {"severity": "warning", "path": "productivity", "message": item}
            for item in productivity["blockers"]
        )
    if requires_live_productivity_gate(manifest, plan):
        if not productivity.get("has_live_productivity_channel"):
            message = (
                "nontrivial paid RunPod launch requires a live productivity channel: "
                "configure startup.progress.http_status_server_port on an exposed http/tcp port "
                "for sanitized progress, or access.ssh_required with SSH/log tail for private workloads"
            )
            if manifest.get("remote_launch_allowed") is True:
                errors.append({"severity": "error", "path": "productivity", "message": message})
            else:
                recommendations.append(message)

    return {
        "ok": not errors,
        "run_id": manifest.get("run_id"),
        "validation": validation.as_dict(),
        "contract": contract,
        "plan": plan,
        "egress": egress,
        "recommended_profile": profile,
        "provider": provider,
        "source": source,
        "payload": payload,
        "bootstrap_requirements": bootstrap,
        "productivity": productivity,
        "errors": errors,
        "warnings": warnings,
        "recommendations": recommendations,
    }


def requires_live_productivity_gate(manifest: dict[str, Any], plan: dict[str, Any]) -> bool:
    if plan.get("task_scale") in ("large", "huge"):
        return True
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    max_runtime = number_or_zero(budget.get("max_runtime_minutes"))
    max_cost = number_or_zero(budget.get("max_estimated_cost_usd"))
    return max_runtime > 30 or max_cost > 5


def number_or_zero(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
