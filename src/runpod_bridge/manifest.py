from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Iterable


PLACEHOLDER_RE = re.compile(r"(replace-with|replace-before|YOUR_|your_|example\.com)")
SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9]{16,}|lin_api_[A-Za-z0-9]{16,})"
)
SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|password|secret|credential|private[_-]?key)", re.I)
SAFE_REF_RE = re.compile(
    r"^(\$|env:|secret:|secure-store:|vault:|aws-sm:|aws-secretsmanager:|aws-sts:|gcp-sm:|azure-kv:|\{\{\s*RUNPOD_SECRET_[A-Za-z0-9_.-]+\s*\}\})"
)
RUNPOD_ENV_LIMIT = 50
BRIDGE_MANAGED_ENV_KEYS = {
    "SYMPHONY_RUN_ID",
    "RUNPOD_BRIDGE_MANAGED",
    "RUNPOD_ENABLE_REPO_BOOTSTRAP",
    "RUNPOD_REPO_DIR",
    "RUNPOD_REPO_SOURCE",
    "RUNPOD_REPO_URL",
    "RUNPOD_REPO_REF",
    "RUNPOD_MAX_RUNTIME_MINUTES",
    "RUNPOD_TERMINATE_AFTER_MINUTES",
}
DURABLE_INTERRUPTIBLE_EGRESS_MODES = {
    "network_volume",
    "runpod_network_volume_s3",
    "scp",
    "object_store_upload",
    "aws_s3_presigned_upload",
}


@dataclass(frozen=True)
class Issue:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[Issue]
    warnings: list[Issue]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.__dict__ for issue in self.errors],
            "warnings": [issue.__dict__ for issue in self.warnings],
        }


class ManifestError(ValueError):
    pass


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{manifest_path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{manifest_path}: manifest root must be an object")
    return data


def validate_manifest(manifest: dict[str, Any]) -> ValidationResult:
    errors: list[Issue] = []
    warnings: list[Issue] = []

    def error(path: str, message: str) -> None:
        errors.append(Issue("error", path, message))

    def warning(path: str, message: str) -> None:
        warnings.append(Issue("warning", path, message))

    remote_launch_allowed = bool(manifest.get("remote_launch_allowed"))
    if remote_launch_allowed:
        launch_authorization = require_object(manifest, "launch_authorization", error)
        if launch_authorization:
            for key in ("source", "approved_by", "approved_at"):
                require_string(launch_authorization, key, f"launch_authorization.{key}", error)

    required_top = [
        "schema_version",
        "manifest_kind",
        "run_id",
        "compute_profile",
        "remote_launch_allowed",
        "budget",
        "repo",
        "access",
        "startup",
        "monitoring",
        "validation_commands",
        "expected_artifacts",
        "artifact_egress",
        "worker_coordination",
        "closeout",
        "safety",
    ]
    for key in required_top:
        if key not in manifest:
            error(key, "required field is missing")

    if manifest.get("manifest_kind") != "symphony_runpod_launch":
        error("manifest_kind", "must be symphony_runpod_launch")

    provider_name = str(get_nested(manifest, ["provider", "name"], "runpod") or "runpod")
    if provider_name != "runpod":
        warning("provider.name", f"provider {provider_name!r} is not implemented; only local validation is available")

    billing = manifest.get("billing", {})
    if billing and not isinstance(billing, dict):
        error("billing", "must be an object when present")
        billing = {}
    if isinstance(billing, dict):
        for key in ("cost_center", "project_code", "resource_owner"):
            if key in billing and not isinstance(billing.get(key), str):
                error(f"billing.{key}", "must be a string")
        if remote_launch_allowed and provider_name == "runpod":
            if not str(billing.get("cost_center") or "").strip():
                warning("billing.cost_center", "recommended for RunPod cost-center attribution and month-end reconciliation")
            if not str(billing.get("project_code") or "").strip():
                warning("billing.project_code", "recommended for local closeout and internal budget mapping")

    budget = require_object(manifest, "budget", error)
    require_positive_number(budget, "max_runtime_minutes", "budget.max_runtime_minutes", error)
    require_positive_number(budget, "max_estimated_cost_usd", "budget.max_estimated_cost_usd", error)
    require_optional_nonnegative_number(budget, "stop_after_minutes", "budget.stop_after_minutes", error)
    require_optional_nonnegative_number(budget, "terminate_after_minutes", "budget.terminate_after_minutes", error)
    if budget:
        max_runtime = float_or_zero(budget.get("max_runtime_minutes"))
        terminate_after = float_or_zero(budget.get("terminate_after_minutes"))
        if terminate_after and max_runtime and terminate_after < max_runtime:
            error("budget.terminate_after_minutes", "must be greater than or equal to budget.max_runtime_minutes")
        if remote_launch_allowed and provider_name == "runpod" and not terminate_after:
            warning("budget.terminate_after_minutes", "recommended for a runpodctl platform-side cleanup backstop")

    repo = require_object(manifest, "repo", error)
    for key in ("source", "url_or_path", "commit_or_snapshot", "workdir"):
        require_string(repo, key, f"repo.{key}", error)
    if remote_launch_allowed and repo:
        source = str(repo.get("source") or "")
        url_or_path = str(repo.get("url_or_path") or "")
        commit_or_snapshot = str(repo.get("commit_or_snapshot") or "")
        if source not in ("git_remote_or_snapshot", "inline_commands"):
            error("repo.source", "remote launch currently requires git_remote_or_snapshot or inline_commands")
        if source == "git_remote_or_snapshot" and not looks_remote_git_source(url_or_path):
            error("repo.url_or_path", "remote pod bootstrap requires an https, http, ssh, or git@ repo source")
        if source == "git_remote_or_snapshot" and not looks_immutable_ref(commit_or_snapshot):
            error("repo.commit_or_snapshot", "remote launch requires an immutable git SHA or snapshot/archive digest reference")
        if source == "inline_commands":
            scale = str(get_nested(manifest, ["workload", "scale"], "") or "")
            if scale != "small":
                error("repo.source", "inline_commands is allowed only for small smoke workloads")
            if str(repo.get("url_or_path") or "") != "inline":
                error("repo.url_or_path", "inline_commands requires repo.url_or_path to be inline")
            if not str(repo.get("commit_or_snapshot") or "").startswith("inline:"):
                error("repo.commit_or_snapshot", "inline_commands requires an inline:<digest-or-id> reference")

    runpod = manifest.get("runpod")
    if provider_name == "runpod":
        if not isinstance(runpod, dict):
            error("runpod", "required object is missing for provider runpod")
        else:
            image_name = str(runpod.get("imageName") or "")
            template_id = str(runpod.get("templateId") or "")
            if not image_name and not template_id:
                error("runpod.imageName", "imageName or templateId is required")
            cloud_type = runpod.get("cloudType")
            if cloud_type not in ("SECURE", "COMMUNITY", None, ""):
                error("runpod.cloudType", "must be SECURE or COMMUNITY")
            require_nonnegative_number(runpod, "gpuCount", "runpod.gpuCount", error, integer=True)
            require_nonnegative_number(runpod, "containerDiskInGb", "runpod.containerDiskInGb", error)
            require_nonnegative_number(runpod, "volumeInGb", "runpod.volumeInGb", error)
            validate_cpu_runtime_shape(runpod, error, warning)
            if not isinstance(runpod.get("ports", []), list):
                error("runpod.ports", "must be an array of port strings")
            elif not all(isinstance(port, str) and re.fullmatch(r"\d+/(http|tcp)", port) for port in runpod.get("ports", [])):
                error("runpod.ports", "ports must use strings such as 8000/http or 22/tcp")
            if not isinstance(runpod.get("env", {}), dict):
                error("runpod.env", "must be an object")
            else:
                env_keys = {str(key) for key in runpod.get("env", {}).keys()}
                projected_env_count = len(env_keys | BRIDGE_MANAGED_ENV_KEYS)
                if projected_env_count > RUNPOD_ENV_LIMIT:
                    error(
                        "runpod.env",
                        f"RunPod allows at most {RUNPOD_ENV_LIMIT} environment variables per Pod; bridge-managed variables leave room for {RUNPOD_ENV_LIMIT - len(BRIDGE_MANAGED_ENV_KEYS)} manifest env vars",
                    )
            if "interruptible" in runpod and not isinstance(runpod.get("interruptible"), bool):
                error("runpod.interruptible", "must be true or false")

    access = require_object(manifest, "access", error)
    if access:
        for key in ("ssh_required", "full_ssh_scp_required", "http_proxy_required", "tcp_ports_required", "public_services_require_auth"):
            require_bool(access, key, f"access.{key}", error)
        if "ssh_public_key_ref" in access and not isinstance(access.get("ssh_public_key_ref"), str):
            error("access.ssh_public_key_ref", "must be a string")

    startup = require_object(manifest, "startup", error)
    if startup:
        if startup.get("mode") not in ("dockerStartCmd", "template", "ssh", "manual"):
            error("startup.mode", "must be dockerStartCmd, template, ssh, or manual")
        commands = startup.get("commands")
        if not isinstance(commands, list) or not commands or not all(isinstance(item, str) and item.strip() for item in commands):
            error("startup.commands", "must be a non-empty array of command strings")
        for key in ("log_file", "status_file", "heartbeat_file"):
            require_string(startup, key, f"startup.{key}", error)
        inspection = startup.get("inspection", {})
        if inspection and not isinstance(inspection, dict):
            error("startup.inspection", "must be an object when present")
        elif isinstance(inspection, dict):
            require_optional_nonnegative_number(inspection, "hold_after_success_seconds", "startup.inspection.hold_after_success_seconds", error, integer=True)
            require_optional_nonnegative_number(inspection, "http_artifact_server_port", "startup.inspection.http_artifact_server_port", error, integer=True)
        progress = startup.get("progress", {})
        if progress and not isinstance(progress, dict):
            error("startup.progress", "must be an object when present")
        elif isinstance(progress, dict):
            require_optional_nonnegative_number(progress, "http_status_server_port", "startup.progress.http_status_server_port", error, integer=True)
            require_optional_nonnegative_number(progress, "log_tail_bytes", "startup.progress.log_tail_bytes", error, integer=True)
            if "include_log_tail" in progress:
                require_bool(progress, "include_log_tail", "startup.progress.include_log_tail", error)
            if "auth_token_ref" in progress and not isinstance(progress.get("auth_token_ref"), str):
                error("startup.progress.auth_token_ref", "must be a string")

    monitoring = require_object(manifest, "monitoring", error)
    if monitoring:
        require_positive_number(monitoring, "poll_interval_seconds", "monitoring.poll_interval_seconds", error)
        require_positive_number(monitoring, "max_silent_minutes", "monitoring.max_silent_minutes", error)

    validation_commands = manifest.get("validation_commands")
    if not isinstance(validation_commands, list) or not validation_commands or not all(isinstance(item, str) and item.strip() for item in validation_commands):
        error("validation_commands", "must be a non-empty array of command strings")

    expected_artifacts = manifest.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        error("expected_artifacts", "must be a non-empty array")
    elif isinstance(expected_artifacts, list):
        for index, artifact in enumerate(expected_artifacts):
            path = f"expected_artifacts[{index}]"
            if not isinstance(artifact, dict):
                error(path, "artifact entry must be an object")
                continue
            require_string(artifact, "artifact_id", f"{path}.artifact_id", error)
            require_string(artifact, "path", f"{path}.path", error)
            if artifact.get("required") is not True:
                warning(f"{path}.required", "artifact is not marked required")

    artifact_egress = require_object(manifest, "artifact_egress", error)
    if artifact_egress:
        mode = artifact_egress.get("mode")
        if mode not in ("workspace_archive", "network_volume", "runpod_network_volume_s3", "scp", "object_store_upload", "aws_s3_presigned_upload"):
            error("artifact_egress.mode", "must be workspace_archive, network_volume, runpod_network_volume_s3, scp, object_store_upload, or aws_s3_presigned_upload")
        if mode == "object_store_upload":
            destination = artifact_egress.get("destination_uri") or artifact_egress.get("destination_uri_ref")
            if not destination:
                message = "object_store_upload requires destination_uri or destination_uri_ref before remote launch"
                if remote_launch_allowed:
                    error("artifact_egress.destination_uri", message)
                else:
                    warning("artifact_egress.destination_uri", message)
            if not artifact_egress.get("credentials_ref"):
                warning("artifact_egress.credentials_ref", "object_store_upload should declare a runtime credential reference")
        if mode == "aws_s3_presigned_upload":
            upload_ref = artifact_egress.get("archive_upload_url_ref") or artifact_egress.get("upload_url_ref")
            literal_upload_url = artifact_egress.get("archive_upload_url") or artifact_egress.get("upload_url")
            if literal_upload_url:
                error("artifact_egress.archive_upload_url", "presigned URLs are bearer credentials and must be injected at runtime with *_url_ref")
            if not upload_ref:
                message = "aws_s3_presigned_upload requires archive_upload_url_ref or upload_url_ref before remote launch"
                if remote_launch_allowed:
                    error("artifact_egress.archive_upload_url_ref", message)
                else:
                    warning("artifact_egress.archive_upload_url_ref", message)
            if artifact_egress.get("requires_presigned_upload") is not True:
                warning("artifact_egress.requires_presigned_upload", "aws_s3_presigned_upload should fail closed with requires_presigned_upload=true")
        if mode in ("network_volume", "runpod_network_volume_s3") or artifact_egress.get("requires_network_volume") is True:
            network_volume_id = str((runpod or {}).get("networkVolumeId") or "") if isinstance(runpod, dict) else ""
            if not network_volume_id:
                message = "network volume egress requires runpod.networkVolumeId"
                if remote_launch_allowed:
                    error("runpod.networkVolumeId", message)
                else:
                    warning("runpod.networkVolumeId", message)
            if isinstance(runpod, dict) and runpod.get("cloudType") != "SECURE":
                error("runpod.cloudType", "RunPod network volumes for Pods require Secure Cloud")
        if mode == "runpod_network_volume_s3":
            datacenter = artifact_egress.get("data_center_id") or artifact_egress.get("s3_endpoint_url") or artifact_egress.get("s3_endpoint_url_ref")
            if not datacenter:
                message = "runpod_network_volume_s3 requires artifact_egress.data_center_id, s3_endpoint_url, or s3_endpoint_url_ref"
                if remote_launch_allowed:
                    error("artifact_egress.data_center_id", message)
                else:
                    warning("artifact_egress.data_center_id", message)
            if not artifact_egress.get("credentials_ref"):
                warning("artifact_egress.credentials_ref", "runpod_network_volume_s3 requires a runtime RunPod S3 API key reference for closeout pull")
    validate_access_requirements(access, runpod if isinstance(runpod, dict) else {}, startup, artifact_egress, remote_launch_allowed, error, warning)

    worker_coordination = require_object(manifest, "worker_coordination", error)
    if worker_coordination:
        if worker_coordination.get("linear_issue_lock_required") is not True:
            error("worker_coordination.linear_issue_lock_required", "must be true")
        if worker_coordination.get("single_mutating_worker") is not True:
            error("worker_coordination.single_mutating_worker", "must be true")
        if not worker_coordination.get("resource_name_prefix"):
            error("worker_coordination.resource_name_prefix", "required for duplicate-launch checks")

    closeout = require_object(manifest, "closeout", error)
    if closeout:
        if closeout.get("stop_or_delete_pod") is not True:
            error("closeout.stop_or_delete_pod", "must be true")
        if closeout.get("linear_outcome_required") is not True:
            error("closeout.linear_outcome_required", "must be true")
        if closeout.get("retain_pod") is True and not closeout.get("retention_approval"):
            message = "retained pods require retention_approval before remote launch"
            if remote_launch_allowed:
                error("closeout.retention_approval", message)
            else:
                warning("closeout.retention_approval", message)

    safety = require_object(manifest, "safety", error)
    if safety and safety.get("no_literal_secrets") is not True:
        error("safety.no_literal_secrets", "must be true")

    workload = manifest.get("workload", {})
    if workload and not isinstance(workload, dict):
        error("workload", "must be an object when present")
    elif isinstance(workload, dict):
        scale = workload.get("scale")
        if scale and scale not in ("small", "medium", "large", "huge"):
            error("workload.scale", "must be small, medium, large, or huge")
        shards = workload.get("shards", [])
        if shards and not isinstance(shards, list):
            error("workload.shards", "must be an array when present")
        if scale in ("large", "huge"):
            checkpoint = workload.get("checkpoint_policy", {})
            if not isinstance(checkpoint, dict) or not checkpoint.get("mode"):
                warning("workload.checkpoint_policy", "large/huge workloads should declare checkpoint policy")
            if artifact_egress and artifact_egress.get("mode") == "workspace_archive" and not artifact_egress.get("requires_network_volume"):
                warning("artifact_egress.mode", "large/huge workloads should consider network_volume, scp, object_store_upload, or aws_s3_presigned_upload egress")

    validate_interruptible_policy(
        manifest,
        runpod if isinstance(runpod, dict) else {},
        artifact_egress if isinstance(artifact_egress, dict) else {},
        remote_launch_allowed,
        error,
        warning,
    )

    scan_for_secrets(manifest, error)
    scan_for_placeholders(manifest, remote_launch_allowed, error, warning)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def build_plan(manifest: dict[str, Any], validation: ValidationResult | None = None) -> dict[str, Any]:
    validation = validation or validate_manifest(manifest)
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    workload = manifest.get("workload", {}) if isinstance(manifest.get("workload"), dict) else {}
    artifact_egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    closeout = manifest.get("closeout", {}) if isinstance(manifest.get("closeout"), dict) else {}
    billing = manifest.get("billing", {}) if isinstance(manifest.get("billing"), dict) else {}

    remote_allowed = bool(manifest.get("remote_launch_allowed"))
    blockers = [issue.message if issue.path == "remote_launch_allowed" else f"{issue.path}: {issue.message}" for issue in validation.errors]
    if not remote_allowed:
        blockers.append("remote_launch_allowed is false; dry-run only")

    scale = workload.get("scale") or infer_scale(manifest)
    provider = str(get_nested(manifest, ["provider", "name"], "runpod") or "runpod")
    estimated_cost_source = "billing_api_preferred" if closeout.get("prefer_billing_api_cost") else "runtime_x_cost_fields"

    return {
        "run_id": manifest.get("run_id"),
        "provider": provider,
        "adapter": "runpod_pod_v1" if provider == "runpod" else f"{provider}_future_adapter",
        "task_scale": scale,
        "remote_launch_allowed": remote_allowed,
        "remote_ready": remote_allowed and validation.ok,
        "blockers": blockers,
        "warnings": [f"{issue.path}: {issue.message}" for issue in validation.warnings],
        "compute": {
            "profile": manifest.get("compute_profile"),
            "gpu_count": runpod.get("gpuCount", 0),
            "gpu_types": runpod.get("gpuTypeIds", []),
            "data_centers": runpod.get("dataCenterIds", []),
            "max_runtime_minutes": budget.get("max_runtime_minutes"),
            "max_estimated_cost_usd": budget.get("max_estimated_cost_usd"),
            "stop_after_minutes": budget.get("stop_after_minutes"),
            "terminate_after_minutes": budget.get("terminate_after_minutes"),
        },
        "execution": {
            "mode": get_nested(manifest, ["startup", "mode"], "dockerStartCmd"),
            "monitoring": manifest.get("monitoring", {}),
            "artifact_egress": artifact_egress,
            "cost_source": estimated_cost_source,
        },
        "cleanup": {
            "stop_or_delete_pod": closeout.get("stop_or_delete_pod"),
            "retain_pod": closeout.get("retain_pod", False),
            "delete_pod_if_network_volume_attached": closeout.get("delete_pod_if_network_volume_attached", True),
        },
        "billing": {
            "cost_center": billing.get("cost_center", ""),
            "project_code": billing.get("project_code", ""),
            "resource_owner": billing.get("resource_owner", ""),
            "cost_source": estimated_cost_source,
        },
    }


def infer_scale(manifest: dict[str, Any]) -> str:
    budget = manifest.get("budget", {}) if isinstance(manifest.get("budget"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    runtime = float_or_zero(budget.get("max_runtime_minutes"))
    cost = float_or_zero(budget.get("max_estimated_cost_usd"))
    gpu_count = int(float_or_zero(runpod.get("gpuCount")))
    volume = float_or_zero(runpod.get("volumeInGb")) + float_or_zero(runpod.get("containerDiskInGb"))
    artifacts = manifest.get("expected_artifacts", [])
    artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
    shards = get_nested(manifest, ["workload", "shards"], [])
    shard_count = len(shards) if isinstance(shards, list) else 0

    if runtime >= 720 or cost >= 100 or shard_count > 8 or volume >= 500:
        return "huge"
    if runtime >= 240 or cost >= 25 or gpu_count > 1 or shard_count > 1 or volume >= 100:
        return "large"
    if runtime >= 60 or gpu_count == 1 or artifact_count > 3:
        return "medium"
    return "small"


def scan_for_placeholders(
    obj: Any,
    remote_launch_allowed: bool,
    error,
    warning,
) -> None:
    for path, value in iter_strings(obj):
        if PLACEHOLDER_RE.search(value):
            message = "placeholder value must be replaced before remote launch"
            if remote_launch_allowed:
                error(path, message)
            else:
                warning(path, message)


def scan_for_secrets(obj: Any, error) -> None:
    for path, value in iter_strings(obj):
        if SECRET_VALUE_RE.search(value):
            error(path, "literal secret-like value detected")
    for path, key, value in iter_key_values(obj):
        if not SECRET_KEY_RE.search(str(key)):
            continue
        if value in (None, ""):
            continue
        if isinstance(value, str) and SAFE_REF_RE.match(value):
            continue
        if isinstance(value, (bool, int, float)):
            continue
        error(path, "sensitive field must use a runtime secret reference, not a literal value")


def validate_interruptible_policy(
    manifest: dict[str, Any],
    runpod: dict[str, Any],
    artifact_egress: dict[str, Any],
    remote_launch_allowed: bool,
    error,
    warning,
) -> None:
    if runpod.get("interruptible") is not True:
        return

    workload = manifest.get("workload", {}) if isinstance(manifest.get("workload"), dict) else {}
    checkpoint = workload.get("checkpoint_policy", {}) if isinstance(workload.get("checkpoint_policy"), dict) else {}
    checkpoint_mode = str(checkpoint.get("mode") or "").strip().lower()
    stage_contract = workload.get("stage_contract", {}) if isinstance(workload.get("stage_contract"), dict) else {}
    resume_policy = str(stage_contract.get("resume_policy") or "").strip().lower()
    egress_mode = str(artifact_egress.get("mode") or "").strip()

    def report(path: str, message: str) -> None:
        if remote_launch_allowed:
            error(path, message)
        else:
            warning(path, message)

    if checkpoint_mode in ("", "none"):
        report("workload.checkpoint_policy", "interruptible Spot Pods require an explicit checkpoint policy before paid launch")
    if not resume_policy or resume_policy in ("none", "not_applicable", "replace-with-rerun-or-checkpoint-policy"):
        report("workload.stage_contract.resume_policy", "interruptible Spot Pods require an explicit resume or rerun policy")
    if egress_mode not in DURABLE_INTERRUPTIBLE_EGRESS_MODES:
        report(
            "artifact_egress.mode",
            "interruptible Spot Pods require durable egress such as network_volume, runpod_network_volume_s3, scp, object_store_upload, or aws_s3_presigned_upload",
        )


def iter_strings(obj: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from iter_strings(value, next_path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from iter_strings(value, f"{path}[{index}]")
    elif isinstance(obj, str):
        yield path, obj


def iter_key_values(obj: Any, path: str = "") -> Iterable[tuple[str, str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield next_path, str(key), value
            yield from iter_key_values(value, next_path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from iter_key_values(value, f"{path}[{index}]")


def require_object(obj: dict[str, Any], key: str, error) -> dict[str, Any]:
    value = obj.get(key)
    if not isinstance(value, dict):
        error(key, "required object is missing or invalid")
        return {}
    return value


def require_string(obj: dict[str, Any], key: str, path: str, error) -> None:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        error(path, "required string is missing or empty")


def require_bool(obj: dict[str, Any], key: str, path: str, error) -> None:
    if not isinstance(obj.get(key), bool):
        error(path, "must be true or false")


def require_positive_number(obj: dict[str, Any], key: str, path: str, error) -> None:
    value = obj.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        error(path, "must be a positive number")


def require_nonnegative_number(obj: dict[str, Any], key: str, path: str, error, *, integer: bool = False) -> None:
    value = obj.get(key)
    expected_type = int if integer else (int, float)
    if not isinstance(value, expected_type) or isinstance(value, bool) or value < 0:
        error(path, "must be a non-negative number")


def require_optional_nonnegative_number(obj: dict[str, Any], key: str, path: str, error, *, integer: bool = False) -> None:
    if key not in obj or obj.get(key) in (None, ""):
        return
    require_nonnegative_number(obj, key, path, error, integer=integer)


def get_nested(obj: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def looks_remote_git_source(value: str) -> bool:
    return value.startswith(("https://", "http://", "ssh://", "git@"))


def looks_immutable_ref(value: str) -> bool:
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        return True
    return value.startswith(("snapshot:", "sha256:", "archive:"))


def validate_access_requirements(
    access: dict[str, Any],
    runpod: dict[str, Any],
    startup: dict[str, Any],
    artifact_egress: dict[str, Any],
    remote_launch_allowed: bool,
    error,
    warning,
) -> None:
    if not access:
        return
    ports = [port for port in runpod.get("ports", []) if isinstance(port, str)]
    inspection = startup.get("inspection", {}) if isinstance(startup.get("inspection"), dict) else {}
    inspection_port = inspection.get("http_artifact_server_port")
    progress = startup.get("progress", {}) if isinstance(startup.get("progress"), dict) else {}
    progress_port = progress.get("http_status_server_port")
    http_proxy_required = access.get("http_proxy_required") is True
    tcp_ports_required = access.get("tcp_ports_required") is True
    ssh_required = access.get("ssh_required") is True
    full_ssh_scp_required = access.get("full_ssh_scp_required") is True
    public_services_auth = access.get("public_services_require_auth") is True

    if http_proxy_required and not has_port(ports, "http"):
        error("runpod.ports", "access.http_proxy_required requires at least one /http port")
    if inspection_port not in (None, ""):
        try:
            inspection_port_int = int(inspection_port)
        except (TypeError, ValueError):
            inspection_port_int = 0
        if inspection_port_int > 0:
            has_http_inspection = has_port(ports, "http", inspection_port_int)
            has_tcp_inspection = has_port(ports, "tcp", inspection_port_int)
            if not http_proxy_required and not tcp_ports_required:
                warning("access.http_proxy_required", "HTTP inspection server should declare http_proxy_required or tcp_ports_required true")
            if not has_http_inspection and not has_tcp_inspection:
                error("runpod.ports", f"startup.inspection.http_artifact_server_port requires {inspection_port_int}/http or {inspection_port_int}/tcp")
            if not public_services_auth:
                warning(
                    "access.public_services_require_auth",
                    "HTTP artifact inspection is public; use only sanitized artifacts or a durable authenticated egress mode",
                )
    if progress_port not in (None, ""):
        try:
            progress_port_int = int(progress_port)
        except (TypeError, ValueError):
            progress_port_int = 0
        if progress_port_int > 0:
            has_http_progress = has_port(ports, "http", progress_port_int)
            has_tcp_progress = has_port(ports, "tcp", progress_port_int)
            if not http_proxy_required and not tcp_ports_required:
                warning("access.http_proxy_required", "HTTP progress server should declare http_proxy_required or tcp_ports_required true")
            if not has_http_progress and not has_tcp_progress:
                error("runpod.ports", f"startup.progress.http_status_server_port requires {progress_port_int}/http or {progress_port_int}/tcp")
            if progress.get("include_log_tail") is True and not str(progress.get("auth_token_ref") or "").strip():
                message = "startup.progress.include_log_tail requires auth_token_ref for remote launches"
                if remote_launch_allowed:
                    error("startup.progress.auth_token_ref", message)
                else:
                    warning("startup.progress.auth_token_ref", message)
            if not public_services_auth and not str(progress.get("auth_token_ref") or "").strip():
                warning(
                    "access.public_services_require_auth",
                    "HTTP progress server is public without auth_token_ref; expose only sanitized health metadata",
                )
    if tcp_ports_required and not has_port(ports, "tcp"):
        error("runpod.ports", "access.tcp_ports_required requires at least one /tcp port")
    if ssh_required and not str(access.get("ssh_public_key_ref") or "").strip():
        error("access.ssh_public_key_ref", "SSH access requires an ssh_public_key_ref")
    if full_ssh_scp_required:
        if not ssh_required:
            error("access.ssh_required", "full SSH/SCP requires ssh_required true")
        if not tcp_ports_required:
            error("access.tcp_ports_required", "full SSH/SCP requires tcp_ports_required true")
        if not has_port(ports, "tcp", 22):
            error("runpod.ports", "full SSH/SCP requires 22/tcp")
        if runpod.get("supportPublicIp") is not True:
            message = "full SSH/SCP requires supportPublicIp=true plus sshd in the image"
            if remote_launch_allowed:
                error("runpod.supportPublicIp", message)
            else:
                warning("runpod.supportPublicIp", message)
    for port in ports:
        if port.endswith("/tcp"):
            left = port.rsplit("/", 1)[0]
            try:
                port_number = int(left)
            except ValueError:
                continue
            if port_number >= 70000:
                warning("runpod.ports", "symmetric TCP pseudo-ports require reading RUNPOD_TCP_PORT_<port> inside the pod")
    if artifact_egress.get("mode") == "scp" and not full_ssh_scp_required:
        error("access.full_ssh_scp_required", "scp artifact egress requires full SSH/SCP")
    if artifact_egress.get("requires_scp") is True and not full_ssh_scp_required:
        error("access.full_ssh_scp_required", "artifact_egress.requires_scp requires full SSH/SCP")
    if remote_launch_allowed and http_proxy_required and not public_services_auth:
        warning("access.public_services_require_auth", "remote HTTP proxy services are public; sanitized smoke data only unless auth is implemented")


def validate_cpu_runtime_shape(runpod: dict[str, Any], error, warning) -> None:
    gpu_count = int(float_or_zero(runpod.get("gpuCount")))
    if gpu_count > 0:
        return
    disk = float_or_zero(runpod.get("containerDiskInGb"))
    raw_cpu_flavors = runpod.get("cpuFlavorIds", [])
    cpu_flavors = [str(item).lower() for item in raw_cpu_flavors] if isinstance(raw_cpu_flavors, list) else []
    if any(item == "cpu3c" or item.startswith("cpu3c-") for item in cpu_flavors) and disk > 20:
        error("runpod.containerDiskInGb", "cpu3c CPU pods should use containerDiskInGb <= 20 to avoid provider rejection")
    elif disk > 20 and not cpu_flavors:
        warning("runpod.containerDiskInGb", "CPU pod containerDiskInGb > 20 can be rejected by small CPU stock such as cpu3c; set cpuFlavorIds or reduce to 20")


def has_port(ports: list[str], protocol: str, internal_port: int | None = None) -> bool:
    expected_port = str(internal_port) if internal_port is not None else None
    for port in ports:
        if "/" not in port:
            continue
        left, right = port.rsplit("/", 1)
        if right != protocol:
            continue
        if expected_port is None or left == expected_port:
            return True
    return False


def dumps_pretty(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
