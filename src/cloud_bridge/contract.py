from __future__ import annotations

import re
from typing import Any

from .manifest import Issue, get_nested, infer_scale


ALLOWED_CLAIM_LEVELS = {
    "artifact_execution_only",
    "unsupported",
    "observed",
    "inferred",
    "candidate",
    "validated",
}
LIVE_OUTPUT_PLACEHOLDER_RE = re.compile(
    r"(^|[^A-Za-z0-9])(mock|fake|dummy|provider[_-]?search|target[_-]?species[_-]?placeholder|placeholder[_-]?output|reference[_-]?output)([^A-Za-z0-9]|$)",
    re.I,
)
SHALLOW_VALIDATION_RE = re.compile(
    r"(^|[;&|]\s*)(which|command\s+-v|pip\s+show|python\d*(\.\d+)?\s+-m\s+pip\s+show|conda\s+list|apt\s+list|dpkg\s+-l)\b"
    r"|(\s|^)(--version|-version|-V)\s*$",
    re.I,
)
STAGE_CONTRACT_FIELDS = {
    "inputs": "non-empty string array",
    "exact_commands": "non-empty string array",
    "expected_outputs": "non-empty string array",
    "done_markers": "non-empty string array",
    "timeout_minutes": "positive number",
    "resume_policy": "non-empty string",
    "fail_closed": "true",
    "claim_level": "allowed claim level",
}
ROUTE_PROOF_FIELDS = {
    "input_materialization": "non-empty string array",
    "tool_invocation": "non-empty string array",
    "artifact_validation": "non-empty string array",
    "claim_boundaries": "non-empty string array",
}


def contract_self_check(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[Issue] = []
    warnings: list[Issue] = []
    recommendations: list[str] = []

    def issue(target: list[Issue], severity: str, path: str, message: str) -> None:
        target.append(Issue(severity, path, message))

    def error(path: str, message: str) -> None:
        issue(errors, "error", path, message)

    def warning(path: str, message: str) -> None:
        issue(warnings, "warning", path, message)

    remote_launch_allowed = bool(manifest.get("remote_launch_allowed"))
    workload = manifest.get("workload", {}) if isinstance(manifest.get("workload"), dict) else {}
    scale = str(workload.get("scale") or infer_scale(manifest))
    stage_contract = workload.get("stage_contract")
    expected_artifacts = manifest.get("expected_artifacts", [])
    validation_commands = manifest.get("validation_commands", [])
    artifact_paths = expected_artifact_paths(expected_artifacts)
    stage_claim_level = ""

    check_output_names(expected_artifacts, remote_launch_allowed, error, warning)
    check_validation_depth(validation_commands, artifact_paths, remote_launch_allowed, error, warning, recommendations)
    check_monitoring_truth(manifest, remote_launch_allowed, error, warning)

    if not isinstance(stage_contract, dict):
        message = "stage contract is missing; declare inputs, exact commands, expected outputs, done markers, timeout, resume behavior, fail-closed policy, and claim level"
        if remote_launch_allowed:
            error("workload.stage_contract", message)
        else:
            warning("workload.stage_contract", message)
    else:
        stage_claim_level = check_stage_contract(
            stage_contract,
            artifact_paths,
            scale,
            remote_launch_allowed,
            error,
            warning,
            recommendations,
        )
        check_route_proof(stage_contract, artifact_paths, remote_launch_allowed, error, warning, recommendations)
        check_exact_command_alignment(
            stage_contract,
            actual_manifest_commands(manifest),
            remote_launch_allowed,
            error,
            warning,
        )
        check_large_run_controls(
            stage_contract,
            workload,
            scale,
            remote_launch_allowed,
            error,
            warning,
            recommendations,
        )

    if scale in ("large", "huge") and not has_real_smoke_reference(stage_contract):
        recommendations.append("large/huge workloads should reference one tiny real smoke using the same route, provider, artifact retrieval, and closeout path")

    if not stage_claim_level:
        closeout_claim = get_nested(manifest, ["closeout", "claim_level"], "")
        stage_claim_level = str(closeout_claim or "artifact_execution_only")
    if stage_claim_level not in ALLOWED_CLAIM_LEVELS:
        target = error if remote_launch_allowed else warning
        target("workload.stage_contract.claim_level", f"claim_level must be one of {sorted(ALLOWED_CLAIM_LEVELS)}")

    return {
        "ok": not errors,
        "run_id": manifest.get("run_id"),
        "scale": scale,
        "stage_contract_present": isinstance(stage_contract, dict),
        "claim_level": stage_claim_level,
        "errors": [item.__dict__ for item in errors],
        "warnings": [item.__dict__ for item in warnings],
        "recommendations": recommendations,
    }


def check_stage_contract(
    stage_contract: dict[str, Any],
    artifact_paths: list[str],
    scale: str,
    remote_launch_allowed: bool,
    error,
    warning,
    recommendations: list[str],
) -> str:
    def target_for_required():
        return error if remote_launch_allowed else warning

    required = target_for_required()
    for field, description in STAGE_CONTRACT_FIELDS.items():
        path = f"workload.stage_contract.{field}"
        value = stage_contract.get(field)
        if field in ("inputs", "exact_commands", "expected_outputs", "done_markers"):
            if not non_empty_string_list(value):
                required(path, f"must be a {description}")
        elif field == "timeout_minutes":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                required(path, f"must be a {description}")
        elif field == "resume_policy":
            if not isinstance(value, str) or not value.strip():
                required(path, f"must be a {description}")
        elif field == "fail_closed":
            if value is not True:
                required(path, "must be true so failed validation cannot close out as success")
        elif field == "claim_level":
            if not isinstance(value, str) or value not in ALLOWED_CLAIM_LEVELS:
                required(path, f"must be one of {sorted(ALLOWED_CLAIM_LEVELS)}")

    expected_outputs = [item.strip() for item in stage_contract.get("expected_outputs", []) if isinstance(item, str)]
    done_markers = [item.strip() for item in stage_contract.get("done_markers", []) if isinstance(item, str)]
    exact_commands = [item.strip() for item in stage_contract.get("exact_commands", []) if isinstance(item, str)]
    inputs = [item.strip() for item in stage_contract.get("inputs", []) if isinstance(item, str)]

    for index, value in enumerate(expected_outputs):
        if LIVE_OUTPUT_PLACEHOLDER_RE.search(value):
            target_for_required()(f"workload.stage_contract.expected_outputs[{index}]", "live outputs cannot use mock, fake, dummy, provider, or placeholder artifact names")

    for artifact_path in artifact_paths:
        if artifact_path and not any(path_mentions(output, artifact_path) for output in expected_outputs):
            warning("workload.stage_contract.expected_outputs", f"declared artifact {artifact_path} is not listed in stage expected_outputs")

    if scale in ("large", "huge"):
        if not done_markers:
            target_for_required()("workload.stage_contract.done_markers", "large/huge workloads require done markers for resumability and closeout")
        if not any("/checkpoint" in item or "checkpoint" in item for item in [*done_markers, *inputs]):
            recommendations.append("large/huge workloads should make checkpoint material visible in inputs or done markers")

    if exact_commands and all(is_shallow_validation(command) for command in exact_commands):
        target_for_required()("workload.stage_contract.exact_commands", "exact commands only prove tool availability; declare the commands that process real inputs and write artifacts")

    return str(stage_contract.get("claim_level") or "")


def check_large_run_controls(
    stage_contract: dict[str, Any],
    workload: dict[str, Any],
    scale: str,
    remote_launch_allowed: bool,
    error,
    warning,
    recommendations: list[str],
) -> None:
    if scale not in ("large", "huge"):
        return

    target = error if remote_launch_allowed else warning

    required_tools = stage_contract.get("required_tools")
    if not valid_required_tools(required_tools):
        target(
            "workload.stage_contract.required_tools",
            "large/huge workloads require fail-closed exact executable proofs before paid launch",
        )

    if not non_empty_string(stage_contract.get("partial_summary_path")):
        target(
            "workload.stage_contract.partial_summary_path",
            "large/huge workloads must declare a partial-summary artifact written on failure, interruption, or degraded closeout",
        )

    cardinality_gate = stage_contract.get("cardinality_gate") or workload.get("cardinality_gate")
    if not isinstance(cardinality_gate, dict):
        target(
            "workload.stage_contract.cardinality_gate",
            "large/huge workloads require a cardinality or fanout gate before expensive execution",
        )
    else:
        for key in ("estimate", "budget_limit", "on_exceed"):
            if not non_empty_string(cardinality_gate.get(key)):
                target(
                    f"workload.stage_contract.cardinality_gate.{key}",
                    "cardinality_gate must declare estimate, budget_limit, and on_exceed behavior",
                )

    fallback_policy = stage_contract.get("fallback_policy") or workload.get("fallback_policy")
    if not isinstance(fallback_policy, dict):
        target(
            "workload.stage_contract.fallback_policy",
            "large/huge workloads require an explicit fallback policy; silent provider, mock, or reduced-scope fallback is forbidden",
        )
    else:
        if fallback_policy.get("no_silent_fallback") is not True:
            target(
                "workload.stage_contract.fallback_policy.no_silent_fallback",
                "must be true so provider or scope fallback cannot be hidden inside a success closeout",
            )
        if fallback_policy.get("degraded_closeout_required") is not True:
            target(
                "workload.stage_contract.fallback_policy.degraded_closeout_required",
                "must be true so fallback or rescue execution closes as degraded or partial unless artifacts prove the original contract",
            )

    if not isinstance(stage_contract.get("normalized_outputs"), list) or not stage_contract.get("normalized_outputs"):
        recommendations.append(
            "large/huge workloads should declare normalized_outputs so raw tool files are converted into stable ledgers before closeout"
        )
    if not isinstance(stage_contract.get("evidence_lanes"), dict):
        recommendations.append(
            "large/huge analytical workloads should separate primary evidence, context evidence, and dossier material in stage_contract.evidence_lanes"
        )
    if not isinstance(stage_contract.get("control_policy"), dict):
        recommendations.append(
            "large/huge analytical workloads should declare positive/negative controls and state that controls are not discoveries"
        )

    checkpoint_policy = workload.get("checkpoint_policy")
    if isinstance(checkpoint_policy, dict) and checkpoint_policy.get("mode") not in (None, "", "none"):
        stale_marker_policy = stage_contract.get("stale_marker_policy")
        if not isinstance(stale_marker_policy, dict):
            recommendations.append(
                "checkpointed workloads should declare stale_marker_policy with input hashes so old done markers cannot satisfy a new run"
            )
        elif stale_marker_policy.get("requires_input_hashes") is not True:
            target(
                "workload.stage_contract.stale_marker_policy.requires_input_hashes",
                "checkpointed workloads must require input hashes before trusting prior done markers",
            )


def valid_required_tools(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not non_empty_string(item.get("name")):
            return False
        if not non_empty_string(item.get("command")):
            return False
        if item.get("fail_closed") is not True:
            return False
    return True


def check_route_proof(
    stage_contract: dict[str, Any],
    artifact_paths: list[str],
    remote_launch_allowed: bool,
    error,
    warning,
    recommendations: list[str],
) -> None:
    route_proof = stage_contract.get("route_proof")
    target = error if remote_launch_allowed else warning
    if not isinstance(route_proof, dict):
        target("workload.stage_contract.route_proof", "must prove input materialization, tool invocation, artifact validation, and claim boundaries")
        return

    for field, description in ROUTE_PROOF_FIELDS.items():
        value = route_proof.get(field)
        if not non_empty_string_list(value):
            target(f"workload.stage_contract.route_proof.{field}", f"must be a {description}")

    artifact_validation = [
        item.strip()
        for item in route_proof.get("artifact_validation", [])
        if isinstance(item, str) and item.strip()
    ]
    tool_invocation = [
        item.strip()
        for item in route_proof.get("tool_invocation", [])
        if isinstance(item, str) and item.strip()
    ]
    if artifact_validation and not any(command_mentions_any_artifact(item, artifact_paths) for item in artifact_validation):
        target("workload.stage_contract.route_proof.artifact_validation", "must inspect declared artifact paths or artifact directory")
    if tool_invocation and all(is_shallow_validation(item) for item in tool_invocation):
        target("workload.stage_contract.route_proof.tool_invocation", "must prove the workload tool route, not only package or executable availability")
    if artifact_validation and any(is_shallow_validation(item) for item in artifact_validation):
        recommendations.append("keep shallow executable checks separate from route_proof.artifact_validation")


def check_exact_command_alignment(
    stage_contract: dict[str, Any],
    actual_commands: list[str],
    remote_launch_allowed: bool,
    error,
    warning,
) -> None:
    target = error if remote_launch_allowed else warning
    exact_commands = [
        item.strip()
        for item in stage_contract.get("exact_commands", [])
        if isinstance(item, str) and item.strip()
    ]
    for index, command in enumerate(exact_commands):
        if not any(commands_match(command, actual) for actual in actual_commands):
            target(
                f"workload.stage_contract.exact_commands[{index}]",
                "exact command is not present in startup.commands, validation_commands, or shard commands",
            )


def check_output_names(expected_artifacts: Any, remote_launch_allowed: bool, error, warning) -> None:
    if not isinstance(expected_artifacts, list):
        return
    target = error if remote_launch_allowed else warning
    for index, artifact in enumerate(expected_artifacts):
        if not isinstance(artifact, dict):
            continue
        for key in ("artifact_id", "path"):
            value = str(artifact.get(key) or "")
            if LIVE_OUTPUT_PLACEHOLDER_RE.search(value):
                target(f"expected_artifacts[{index}].{key}", "live artifact names cannot contain mock, fake, dummy, provider_search, or placeholder output markers")


def check_validation_depth(
    validation_commands: Any,
    artifact_paths: list[str],
    remote_launch_allowed: bool,
    error,
    warning,
    recommendations: list[str],
) -> None:
    if not isinstance(validation_commands, list):
        return
    commands = [command for command in validation_commands if isinstance(command, str)]
    if not commands:
        return
    artifact_checks = [command for command in commands if command_mentions_any_artifact(command, artifact_paths)]
    shallow_checks = [command for command in commands if is_shallow_validation(command)]
    target = error if remote_launch_allowed else warning
    if not artifact_checks:
        target("validation_commands", "validation must inspect declared artifacts, not only tool installation, pod state, or command availability")
    if shallow_checks and not artifact_checks:
        target("validation_commands", "validation commands only prove packages or executables; add artifact-level checks")
    elif shallow_checks:
        recommendations.append("keep package/executable checks as preflight, but make live readiness depend on artifact-level validation")


def check_monitoring_truth(manifest: dict[str, Any], remote_launch_allowed: bool, error, warning) -> None:
    monitoring = manifest.get("monitoring", {}) if isinstance(manifest.get("monitoring"), dict) else {}
    target = error if remote_launch_allowed else warning
    for key in ("requires_workload_heartbeat", "requires_status_file", "requires_log_artifact"):
        if monitoring.get(key) is not True:
            target(f"monitoring.{key}", "must be true so provider state is not treated as workload success")


def has_real_smoke_reference(stage_contract: Any) -> bool:
    if not isinstance(stage_contract, dict):
        return False
    smoke = stage_contract.get("real_smoke") or stage_contract.get("smoke_test")
    if not isinstance(smoke, dict):
        return False
    return bool(smoke.get("same_route") is True and smoke.get("artifact_verified") is True)


def actual_manifest_commands(manifest: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    startup_commands = startup.get("commands", [])
    if isinstance(startup_commands, list):
        commands.extend(str(item) for item in startup_commands if isinstance(item, str) and item.strip())
    validation_commands = manifest.get("validation_commands", [])
    if isinstance(validation_commands, list):
        commands.extend(str(item) for item in validation_commands if isinstance(item, str) and item.strip())
    shards = get_nested(manifest, ["workload", "shards"], [])
    if isinstance(shards, list):
        for shard in shards:
            if isinstance(shard, dict) and isinstance(shard.get("command"), str) and shard["command"].strip():
                commands.append(str(shard["command"]))
    return commands


def commands_match(declared: str, actual: str) -> bool:
    left = normalize_command(declared)
    right = normalize_command(actual)
    return bool(left and right and (left == right or left in right or right in left))


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def expected_artifact_paths(expected_artifacts: Any) -> list[str]:
    if not isinstance(expected_artifacts, list):
        return []
    paths: list[str] = []
    for artifact in expected_artifacts:
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
    return paths


def command_mentions_any_artifact(command: str, artifact_paths: list[str]) -> bool:
    return any(path_mentions(command, artifact_path) for artifact_path in artifact_paths)


def path_mentions(text: str, artifact_path: str) -> bool:
    if not artifact_path:
        return False
    normalized_text = text.replace("\\", "/")
    normalized_path = artifact_path.replace("\\", "/")
    if normalized_path in normalized_text:
        return True
    parts = [part for part in normalized_path.split("/") if part]
    if not parts:
        return False
    basename = parts[-1]
    parent = "/".join(parts[:-1])
    return bool(basename and basename in normalized_text) or bool(parent and parent in normalized_text)


def is_shallow_validation(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    return bool(SHALLOW_VALIDATION_RE.search(stripped))


def non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
