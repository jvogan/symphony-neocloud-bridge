from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import shlex
from typing import Any

from .egress import build_egress_plan
from .manifest import get_nested, iter_strings
from .providers import AWS_DOCS, RUNPOD_DOCS


ECR_IMAGE_RE = re.compile(r"^(?P<registry>\d{12}\.dkr\.ecr(?:-fips)?\.(?P<region>[a-z0-9-]+)\.amazonaws\.com(?:\.cn)?)/")


def build_aws_orchestrator_plan(
    manifest: dict[str, Any],
    *,
    handoff_path: str = "provider_handoff.json",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now_utc = normalize_datetime(now_utc)
    aws_config = manifest.get("aws", {}) if isinstance(manifest.get("aws"), dict) else {}
    features = {
        "sts_scoped_object_store_upload": sts_object_store_plan(manifest, aws_config),
        "runpod_network_volume_s3": runpod_network_volume_s3_plan(manifest),
        "ecr_registry_refresh": ecr_registry_refresh_plan(manifest, aws_config),
        "secrets_manager_refs": secrets_manager_plan(manifest),
        "sqs_handoff_queue": sqs_handoff_queue_plan(manifest, aws_config, handoff_path),
        "dynamodb_launch_lock": dynamodb_launch_lock_plan(manifest, aws_config, now_utc),
        "eventbridge_cleanup_backstop": eventbridge_cleanup_plan(manifest, aws_config, now_utc),
    }
    warnings: list[str] = []
    blockers: list[str] = []
    required_env: list[str] = []
    for feature in features.values():
        warnings.extend(feature.get("warnings", []))
        blockers.extend(feature.get("blockers", []))
        required_env.extend(feature.get("required_env", []))
    return {
        "ok": not blockers,
        "run_id": manifest.get("run_id"),
        "docs_checked": {
            "aws": AWS_DOCS,
            "runpod": {
                "s3_api": RUNPOD_DOCS["s3_api"],
                "runpodctl_registry": RUNPOD_DOCS["runpodctl_registry"],
            },
        },
        "required_env": sorted(set(required_env)),
        "blockers": blockers,
        "warnings": warnings,
        "features": features,
    }


def sts_object_store_plan(manifest: dict[str, Any], aws_config: dict[str, Any]) -> dict[str, Any]:
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    mode = str(egress.get("mode") or "")
    credentials_ref = str(egress.get("credentials_ref") or "")
    role_ref = str(aws_config.get("artifact_role_arn_ref") or "")
    if credentials_ref.startswith("aws-sts:"):
        role_ref = credentials_ref
    destination_uri = str(egress.get("destination_uri") or "")
    destination_ref = str(egress.get("destination_uri_ref") or "")
    required_env: list[str] = []
    warnings: list[str] = []
    helper_files: dict[str, Any] = {}
    commands: list[str] = []

    if mode != "object_store_upload":
        return feature("not_configured", "object_store_upload is not declared; STS-scoped object-store upload is not needed")
    if not role_ref:
        return feature(
            "recommended",
            "object_store_upload is declared; use credentials_ref=aws-sts:<role-arn> or aws.artifact_role_arn_ref for short-lived credentials",
            warnings=["object_store_upload should use presigned URLs or short-lived STS credentials instead of long-lived access keys"],
        )

    role_expr, role_env = shell_ref(role_ref, "RUNPOD_BRIDGE_AWS_ARTIFACT_ROLE_ARN")
    if role_env:
        required_env.append(role_env)
    session_name = safe_name(f"runpod-bridge-{manifest.get('run_id') or 'run'}", max_length=64)
    duration_seconds = sts_duration_seconds(manifest)
    policy_file = ""
    if destination_uri.startswith("s3://"):
        policy_file = "aws-sts-s3-upload-policy.json"
        helper_files[policy_file] = build_s3_upload_session_policy(destination_uri)
    elif destination_ref.startswith("env:"):
        required_env.append(destination_ref.split(":", 1)[1])
        warnings.append("destination_uri_ref prevents static session-policy generation; bind the STS role policy to the exact run prefix")
    else:
        warnings.append("destination_uri is not a literal s3:// URI; bind the STS role policy to the exact run prefix")

    command = [
        "aws",
        "sts",
        "assume-role",
        "--role-arn",
        role_expr,
        "--role-session-name",
        shlex.quote(session_name),
        "--duration-seconds",
        str(duration_seconds),
    ]
    if policy_file:
        command.extend(["--policy", f"file://{policy_file}"])
    credential_file = f".runtime/aws-sts-{safe_name(str(manifest.get('run_id') or 'run'))}.json"
    command.extend([">", credential_file])
    commands.append(" ".join(command))
    commands.append(
        "python3 -c 'import json, pathlib, sys; "
        "c=json.load(open(sys.argv[1]))[\"Credentials\"]; "
        "fields=[(\"AWS_ACCESS_KEY_ID\",\"AccessKeyId\"),(\"AWS_SECRET_ACCESS_KEY\",\"SecretAccessKey\"),(\"AWS_SESSION_TOKEN\",\"SessionToken\")]; "
        "pathlib.Path(sys.argv[2]).write_text(\"\\n\".join(\"export \" + env + \"=\\\"\" + c[key] + \"\\\"\" for env,key in fields) + \"\\n\")' "
        f"{credential_file} {credential_file}.env"
    )
    commands.append(f"source {credential_file}.env")
    warnings.append("STS credential files contain secrets; write them only under ignored runtime directories and delete them at closeout")
    return feature(
        "configured",
        "STS-scoped object-store upload is configured for the artifact egress lane",
        commands=commands,
        required_env=required_env,
        warnings=warnings,
        helper_files=helper_files,
    )


def runpod_network_volume_s3_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    egress = manifest.get("artifact_egress", {}) if isinstance(manifest.get("artifact_egress"), dict) else {}
    if egress.get("mode") != "runpod_network_volume_s3":
        if get_nested(manifest, ["runpod", "networkVolumeId"], ""):
            return feature("available", "RunPod network volume is declared; S3-compatible pull can be enabled with artifact_egress.mode=runpod_network_volume_s3")
        return feature("not_configured", "RunPod network-volume S3 egress is not declared")
    plan = build_egress_plan(manifest)
    required_env = list(plan.get("required_env", []))
    warnings = list(plan.get("warnings", []))
    warnings.append("RunPod S3 API keys are separate from RUNPOD_API_KEY; inject them only on the trusted orchestrator host")
    return feature(
        "configured" if plan.get("ok") else "blocked",
        "RunPod network-volume S3 closeout is configured",
        commands=list(plan.get("commands", [])),
        required_env=required_env,
        warnings=warnings,
        blockers=list(plan.get("blockers", [])),
    )


def ecr_registry_refresh_plan(manifest: dict[str, Any], aws_config: dict[str, Any]) -> dict[str, Any]:
    ecr = aws_config.get("ecr", {}) if isinstance(aws_config.get("ecr"), dict) else {}
    image_name = str(get_nested(manifest, ["runpod", "imageName"], "") or "")
    match = ECR_IMAGE_RE.search(image_name)
    registry_uri = str(ecr.get("registry_uri") or (match.group("registry") if match else ""))
    region = str(ecr.get("region") or (match.group("region") if match else ""))
    region_ref = str(ecr.get("region_ref") or aws_config.get("region_ref") or "")
    registry_auth_name = str(ecr.get("runpod_registry_auth_name") or safe_name(f"{manifest.get('run_id') or 'run'}-ecr"))
    if not registry_uri:
        return feature("not_configured", "No ECR image or aws.ecr.registry_uri found")
    region_expr, region_env = shell_ref(region_ref or region, "AWS_REGION")
    required_env = [region_env] if region_env else []
    commands = [
        "set +x",
        f'RUNPOD_ECR_PASSWORD="$(aws ecr get-login-password --region {region_expr})"',
        f"runpodctl registry create --name {shlex.quote(registry_auth_name)} --username AWS --password \"$RUNPOD_ECR_PASSWORD\"",
        "unset RUNPOD_ECR_PASSWORD",
        "runpodctl registry list",
    ]
    warnings = [
        "runpodctl registry create accepts the password as a flag; run this only on a trusted host with shell tracing disabled",
        "ECR auth tokens expire; refresh registry auth immediately before RunPod launch and delete stale registry auth records",
    ]
    if not get_nested(manifest, ["runpod", "containerRegistryAuthId"], ""):
        warnings.append("runpod.containerRegistryAuthId is empty; the created registry auth ID must be injected before launch")
    return feature(
        "configured",
        "ECR registry refresh through RunPod registry auth is available for this image",
        commands=commands,
        required_env=required_env,
        warnings=warnings,
    )


def secrets_manager_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    refs = []
    for path, value in iter_strings(manifest):
        if value.startswith("aws-sm:"):
            refs.append({"path": path, "secret_id": value.split(":", 1)[1]})
        elif value.startswith("aws-secretsmanager:"):
            refs.append({"path": path, "secret_id": value.split(":", 1)[1]})
    if not refs:
        return feature("not_configured", "No AWS Secrets Manager refs found")
    commands = [
        f"aws secretsmanager get-secret-value --secret-id {shlex.quote(ref['secret_id'])} --query SecretString --output text"
        for ref in refs
    ]
    return feature(
        "configured",
        "AWS Secrets Manager refs are declared and must be resolved by the orchestrator",
        commands=commands,
        warnings=["Secrets Manager command output is sensitive; never write it to manifests, Linear, or normal logs"],
        refs=refs,
    )


def sqs_handoff_queue_plan(manifest: dict[str, Any], aws_config: dict[str, Any], handoff_path: str) -> dict[str, Any]:
    sqs = aws_config.get("sqs", {}) if isinstance(aws_config.get("sqs"), dict) else {}
    queue_ref = str(sqs.get("queue_url_ref") or aws_config.get("sqs_handoff_queue_url_ref") or "")
    fifo = bool(sqs.get("fifo"))
    visibility_timeout = int(sqs.get("visibility_timeout_seconds") or 300)
    if not queue_ref:
        return feature("recommended", "SQS handoff queue is not configured; add aws.sqs.queue_url_ref for multi-host orchestrators")
    queue_expr, queue_env = shell_ref(queue_ref, "RUNPOD_BRIDGE_SQS_QUEUE_URL")
    required_env = [queue_env] if queue_env else []
    compact_body = "python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(\",\",\":\")))'"
    send = f"aws sqs send-message --queue-url {queue_expr} --message-body \"$({compact_body} {shlex.quote(handoff_path)})\""
    if fifo:
        run_id = safe_name(str(manifest.get("run_id") or "run"))
        send += f" --message-group-id {shlex.quote(run_id)} --message-deduplication-id {shlex.quote(run_id)}-$(date -u +%Y%m%dT%H%M%SZ)"
    commands = [
        send,
        f"aws sqs receive-message --queue-url {queue_expr} --max-number-of-messages 1 --wait-time-seconds 20 --visibility-timeout {visibility_timeout} --message-attribute-names All",
        f"aws sqs change-message-visibility --queue-url {queue_expr} --receipt-handle \"$RUNPOD_BRIDGE_SQS_RECEIPT_HANDLE\" --visibility-timeout {visibility_timeout}",
        f"aws sqs delete-message --queue-url {queue_expr} --receipt-handle \"$RUNPOD_BRIDGE_SQS_RECEIPT_HANDLE\"",
    ]
    return feature(
        "configured",
        "SQS handoff queue commands are ready for orchestrator-side dispatch",
        commands=commands,
        required_env=required_env,
        warnings=["SQS is at-least-once; keep DynamoDB/local launch locking and make handoff execution idempotent"],
    )


def dynamodb_launch_lock_plan(manifest: dict[str, Any], aws_config: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    dynamodb = aws_config.get("dynamodb", {}) if isinstance(aws_config.get("dynamodb"), dict) else {}
    table_ref = str(dynamodb.get("lock_table_ref") or aws_config.get("dynamodb_lock_table_ref") or "")
    ttl_seconds = int(dynamodb.get("ttl_seconds") or aws_config.get("dynamodb_lock_ttl_seconds") or 3600)
    if not table_ref:
        return feature("recommended", "DynamoDB launch lock is not configured; add aws.dynamodb.lock_table_ref for multi-host orchestrators")
    table_expr, table_env = shell_ref(table_ref, "RUNPOD_BRIDGE_LOCK_TABLE")
    required_env = [table_env, "RUNPOD_BRIDGE_OWNER_ID"] if table_env else ["RUNPOD_BRIDGE_OWNER_ID"]
    key = f"runpod-bridge#{get_nested(manifest, ['worker_coordination', 'resource_name_prefix'], manifest.get('run_id') or 'run')}"
    now_epoch = int(now_utc.timestamp())
    expires_epoch = now_epoch + ttl_seconds
    helper_files = {
        "aws-dynamodb-lock-put.template.json": {
            "pk": {"S": key},
            "run_id": {"S": str(manifest.get("run_id") or "")},
            "owner_id": {"S": "${RUNPOD_BRIDGE_OWNER_ID}"},
            "created_at": {"S": now_utc.isoformat().replace("+00:00", "Z")},
            "expires_at": {"N": str(expires_epoch)},
        },
        "aws-dynamodb-lock-condition-values.template.json": {
            ":now": {"N": str(now_epoch)},
            ":owner": {"S": "${RUNPOD_BRIDGE_OWNER_ID}"},
        },
        "aws-dynamodb-lock-key.json": {
            "pk": {"S": key},
        },
    }
    commands = [
        render_template_command("aws-dynamodb-lock-put.template.json", "aws-dynamodb-lock-condition-values.template.json"),
        f'aws dynamodb put-item --table-name {table_expr} --item file://aws-dynamodb-lock-put.json --condition-expression "attribute_not_exists(pk) OR expires_at < :now" --expression-attribute-values file://aws-dynamodb-lock-condition-values.json',
        f'aws dynamodb delete-item --table-name {table_expr} --key file://aws-dynamodb-lock-key.json --condition-expression "owner_id = :owner" --expression-attribute-values file://aws-dynamodb-lock-condition-values.json',
    ]
    return feature(
        "configured",
        "DynamoDB launch lock command templates are ready",
        commands=commands,
        required_env=required_env,
        warnings=[
            "DynamoDB TTL cleanup is eventual; use conditional release and do not rely on TTL for immediate unlock",
            "Helper templates contain env placeholders; render them only on the trusted orchestrator host",
        ],
        helper_files=helper_files,
    )


def eventbridge_cleanup_plan(manifest: dict[str, Any], aws_config: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    cleanup = aws_config.get("eventbridge_cleanup", {}) if isinstance(aws_config.get("eventbridge_cleanup"), dict) else {}
    enabled = cleanup.get("enabled") is True
    terminate_after = int(float(get_nested(manifest, ["budget", "terminate_after_minutes"], 0) or 0))
    if not enabled:
        if terminate_after:
            return feature("recommended", "budget.terminate_after_minutes is set; add aws.eventbridge_cleanup to get an orchestrator-death cleanup backstop")
        return feature("not_configured", "EventBridge cleanup backstop is not configured")
    role_ref = str(cleanup.get("role_arn_ref") or cleanup.get("schedule_role_arn_ref") or "")
    target_ref = str(cleanup.get("target_arn_ref") or "")
    if not role_ref or not target_ref:
        return feature(
            "blocked",
            "EventBridge cleanup is enabled but role_arn_ref and target_arn_ref are required",
            blockers=["aws.eventbridge_cleanup.role_arn_ref and target_arn_ref are required when enabled"],
        )
    role_expr, role_env = shell_ref(role_ref, "RUNPOD_BRIDGE_SCHEDULER_ROLE_ARN")
    target_expr, target_env = shell_ref(target_ref, "RUNPOD_BRIDGE_CLEANUP_TARGET_ARN")
    dlq_ref = str(cleanup.get("dead_letter_queue_arn_ref") or "")
    dlq_expr, dlq_env = shell_ref(dlq_ref, "RUNPOD_BRIDGE_CLEANUP_DLQ_ARN") if dlq_ref else ("", "")
    required_env = [env for env in (role_env, target_env, dlq_env) if env]
    schedule_name = safe_name(str(cleanup.get("schedule_name") or f"runpod-{manifest.get('run_id') or 'run'}-cleanup"), max_length=64)
    minutes = terminate_after or int(float(get_nested(manifest, ["budget", "max_runtime_minutes"], 60) or 60))
    schedule_at = now_utc + timedelta(minutes=minutes)
    input_payload = {
        "run_id": manifest.get("run_id"),
        "resource_name_prefix": get_nested(manifest, ["worker_coordination", "resource_name_prefix"], ""),
        "cleanup_action": str(cleanup.get("cleanup_action") or "delete"),
    }
    target_file = {
        "Arn": target_expr,
        "RoleArn": role_expr,
        "Input": json.dumps(input_payload, separators=(",", ":")),
    }
    if dlq_expr:
        target_file["DeadLetterConfig"] = {"Arn": dlq_expr}
    helper_files = {"aws-eventbridge-cleanup-target.template.json": target_file}
    group_ref = str(cleanup.get("group_name_ref") or "")
    group_flag = ""
    if group_ref:
        group_expr, group_env = shell_ref(group_ref, "RUNPOD_BRIDGE_SCHEDULER_GROUP")
        group_flag = f" --group-name {group_expr}"
        if group_env:
            required_env.append(group_env)
    commands = [
        render_template_command("aws-eventbridge-cleanup-target.template.json"),
        f"aws scheduler create-schedule --name {shlex.quote(schedule_name)}{group_flag} --schedule-expression at({schedule_at.strftime('%Y-%m-%dT%H:%M:%S')}) --flexible-time-window '{{\"Mode\":\"OFF\"}}' --target file://aws-eventbridge-cleanup-target.json --action-after-completion DELETE",
        f"aws scheduler delete-schedule --name {shlex.quote(schedule_name)}{group_flag}",
    ]
    return feature(
        "configured",
        "EventBridge Scheduler one-time cleanup backstop is configured",
        commands=commands,
        required_env=sorted(set(required_env)),
        warnings=[
            "EventBridge cleanup is a backstop; normal bridge cleanup and artifact closeout still own success",
            "Helper templates contain env placeholders; render them only on the trusted orchestrator host",
        ],
        helper_files=helper_files,
    )


def feature(
    status: str,
    summary: str,
    *,
    commands: list[str] | None = None,
    required_env: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    helper_files: dict[str, Any] | None = None,
    refs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "commands": commands or [],
        "required_env": sorted(set(required_env or [])),
        "warnings": warnings or [],
        "blockers": blockers or [],
    }
    if helper_files:
        payload["helper_files"] = helper_files
    if refs is not None:
        payload["refs"] = refs
    return payload


def build_s3_upload_session_policy(destination_uri: str) -> dict[str, Any]:
    bucket, prefix = parse_s3_uri(destination_uri)
    object_arn = f"arn:aws:s3:::{bucket}/{prefix.rstrip('/') + '/' if prefix else ''}*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
                "Resource": object_arn,
            }
        ],
    }


def render_template_command(*filenames: str) -> str:
    script = (
        "import os,pathlib,sys; "
        "[pathlib.Path(str(p).replace('.template.json','.json')).write_text(os.path.expandvars(p.read_text())) "
        "for p in map(pathlib.Path, sys.argv[1:])]"
    )
    return "python3 -c " + shlex.quote(script) + " " + " ".join(shlex.quote(name) for name in filenames)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    remainder = uri[len("s3://") :]
    if "/" not in remainder:
        return remainder, ""
    bucket, prefix = remainder.split("/", 1)
    return bucket, prefix


def shell_ref(ref: str, default_env: str) -> tuple[str, str]:
    if ref.startswith("env:"):
        env_name = ref.split(":", 1)[1].strip()
        return f"${env_name}", env_name
    if ref.startswith("aws-sts:"):
        return shlex.quote(ref.split(":", 1)[1]), ""
    if ref:
        return shlex.quote(ref), ""
    return f"${default_env}", default_env


def safe_name(value: str, *, max_length: int = 64) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return (cleaned or "run")[:max_length]


def sts_duration_seconds(manifest: dict[str, Any]) -> int:
    max_runtime_minutes = int(float(get_nested(manifest, ["budget", "max_runtime_minutes"], 60) or 60))
    requested = max(900, min(43200, (max_runtime_minutes + 15) * 60))
    return requested


def normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
