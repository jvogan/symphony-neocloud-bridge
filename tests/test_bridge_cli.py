import copy
import io
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout

import cloud_bridge.cli as cli_module
import cloud_bridge.proxy as proxy_module
import cloud_bridge.remote_run as remote_run_module
import cloud_bridge.providers.runpod.rest as runpod_rest_module
from cloud_bridge.aws_orchestration import build_aws_orchestrator_plan
from cloud_bridge.bootstrap_requirements import bootstrap_requirements_report
from cloud_bridge.cli import main as cli_main
from cloud_bridge.closeout import write_closeout_files
from cloud_bridge.contract import contract_self_check
from cloud_bridge.cost import cost_report_from_record
from cloud_bridge.dashboard import scan_dashboard_records, write_dashboard
from cloud_bridge.doctor import run_doctor
from cloud_bridge.egress import build_egress_plan
from cloud_bridge.handoff import (
    run_handoff_flow,
    validate_provider_handoff,
    write_provider_handoff,
)
from cloud_bridge.linear_issue import validate_issue_file
from cloud_bridge.linear_api import issue_identifier, issue_to_markdown
from cloud_bridge.local_run import run_local
from cloud_bridge.manifest import build_plan, load_manifest, validate_manifest
from cloud_bridge.manifest_audit import audit_manifest_tree, build_migration_hints
from cloud_bridge.monitor import inspect_execution
from cloud_bridge.orchestrator import issue_intake, scan_handoffs
from cloud_bridge.packet import prepare_packet
from cloud_bridge.payload import MAX_POST_BODY_BYTES, create_request_payload_report
from cloud_bridge.preflight import analyze_preflight
from cloud_bridge.productivity import build_productivity_plan
import cloud_bridge.progress_report as progress_report_module
from cloud_bridge.progress_report import classify_progress, fetch_live_progress, redact_sensitive_text
from cloud_bridge.profiles import get_profile, recommend_profile
from cloud_bridge.providers import provider_capabilities
from cloud_bridge.proxy import proxy_url, required_proxy_paths, tcp_endpoint_from_pod, tcp_endpoint_from_runtime_report, tcp_url
from cloud_bridge.public_readiness import run_public_audit
from cloud_bridge.registry_auth import build_registry_auth_plan
from cloud_bridge.recovery import analyze_recovery
from cloud_bridge.remote_outcome import write_remote_outcome
from cloud_bridge.remote_run import acquire_launch_lock, release_launch_lock, run_remote_flow
from cloud_bridge.providers.runpod.rest import (
    RunpodRestError,
    active_duplicate_pods,
    build_remote_launch_preview,
    build_create_pod_request,
    cleanup_pod_flow,
    create_pod_flow,
    verify_cleanup,
)
from cloud_bridge.providers.runpod.catalog import build_gpu_catalog_report
from cloud_bridge.providers.runpod.ops_audit import audit_runpod_ops_tree
from cloud_bridge.providers.runpod.runtime import analyze_runtime_metrics, build_runtime_metrics_report
from cloud_bridge.providers.runpod.s3_verify import build_network_volume_s3_verify_plan, safe_extract, verify_network_volume_s3
from cloud_bridge.providers.runpod.ctl import billing_pods_command, build_pod_create_command, shell_join
from cloud_bridge.source_check import check_source_reachability
from cloud_bridge.source_archive import prepare_source_archive
from cloud_bridge.source_ingress import build_source_ingress_plan
from cloud_bridge.startup import render_startup_script
from cloud_bridge.supervisor import supervise_execution


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "runpod-launch-manifest.template.json"


class BridgeTests(unittest.TestCase):
    def manifest(self):
        return load_manifest(TEMPLATE)

    def test_template_is_valid_for_dry_run(self):
        result = validate_manifest(self.manifest())
        self.assertTrue(result.ok)
        self.assertTrue(result.warnings)

    def test_remote_launch_with_placeholders_is_blocked(self):
        manifest = self.manifest()
        manifest["remote_launch_allowed"] = True
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("placeholder" in issue.message for issue in result.errors))

    def test_contract_self_check_blocks_live_mock_artifact(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["expected_artifacts"][0]["artifact_id"] = "mock_result"
        report = contract_self_check(manifest)
        self.assertFalse(report["ok"])
        self.assertTrue(any("mock" in issue["message"] for issue in report["errors"]))

    def test_contract_self_check_rejects_tool_only_validation(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["validation_commands"] = ["python3 --version"]
        report = contract_self_check(manifest)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue["path"] == "validation_commands" for issue in report["errors"]))

    def test_contract_self_check_requires_route_proof_for_remote(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        del manifest["workload"]["stage_contract"]["route_proof"]
        report = contract_self_check(manifest)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue["path"] == "workload.stage_contract.route_proof" for issue in report["errors"]))

    def test_huge_contract_declares_stage_and_smoke(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        report = contract_self_check(manifest)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["stage_contract_present"])
        self.assertEqual(report["claim_level"], "artifact_execution_only")

    def test_large_remote_contract_requires_scale_gates(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-04T00:00:00Z",
        }
        manifest["workload"]["scale"] = "large"
        report = contract_self_check(manifest)
        self.assertFalse(report["ok"])
        error_paths = {issue["path"] for issue in report["errors"]}
        self.assertIn("workload.stage_contract.required_tools", error_paths)
        self.assertIn("workload.stage_contract.partial_summary_path", error_paths)
        self.assertIn("workload.stage_contract.cardinality_gate", error_paths)
        self.assertIn("workload.stage_contract.fallback_policy", error_paths)

    def test_huge_remote_contract_accepts_scale_gates(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-04T00:00:00Z",
        }
        report = contract_self_check(manifest)
        self.assertTrue(report["ok"], report)

    def test_remote_launch_requires_immutable_ref(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "main"
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "repo.commit_or_snapshot" for issue in result.errors))

    def test_literal_secret_in_env_is_blocked(self):
        manifest = self.manifest()
        env_key = "API" + "_KEY"
        env_value = "-".join(["literal", "secret", "value"])
        manifest["runpod"]["env"] = {env_key: env_value}
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("secret" in issue.message for issue in result.errors))

    def test_runpod_secret_reference_in_env_is_allowed(self):
        manifest = self.manifest()
        manifest["runpod"]["env"] = {"API_KEY": "{{ RUNPOD_SECRET_demo_api_key }}"}
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.errors)

    def test_env_limit_accounts_for_bridge_managed_variables(self):
        manifest = self.manifest()
        manifest["runpod"]["env"] = {f"USER_ENV_{index}": "value" for index in range(42)}
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "runpod.env" for issue in result.errors))

    def test_community_cloud_remote_launch_requires_explicit_public_synthetic_opt_in(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-15T00:00:00Z",
        }
        manifest["runpod"]["cloudType"] = "COMMUNITY"
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "runpod.cloudType" for issue in result.errors))

        manifest["safety"]["community_cloud_allowed"] = True
        manifest["safety"]["private_data_policy"] = "public synthetic sanitized smoke only"
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.as_dict())

    def test_interruptible_remote_launch_requires_checkpoint_and_durable_egress(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-13T00:00:00Z",
        }
        manifest["runpod"]["interruptible"] = True
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        error_paths = {issue.path for issue in result.errors}
        self.assertIn("workload.checkpoint_policy", error_paths)
        self.assertIn("artifact_egress.mode", error_paths)

    def test_cpu3c_disk_cap_blocks_before_paid_launch(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["runpod"]["gpuCount"] = 0
        manifest["runpod"]["cpuFlavorIds"] = ["cpu3c"]
        manifest["runpod"]["containerDiskInGb"] = 40
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "runpod.containerDiskInGb" for issue in result.errors))

    def test_source_check_renders_git_reachability_plan(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["remote_launch_allowed"] = True
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        report = check_source_reachability(manifest)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["status"], "not_executed")
        self.assertTrue(any("git -C" in command for command in report["commands"]))

    def test_bootstrap_requirements_block_remote_git_without_declared_git(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["remote_launch_allowed"] = True
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        manifest["runpod"]["imageName"] = "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime"
        report = bootstrap_requirements_report(manifest)
        self.assertFalse(report["ok"])
        self.assertTrue(report["requires_git"])
        self.assertTrue(any(issue["path"] == "runpod.image_capabilities" for issue in report["errors"]))

        manifest["runpod"]["image_capabilities"] = ["git"]
        manifest["repo"]["source_proof"] = {"status": "reachable", "url": manifest["repo"]["url_or_path"], "ref": manifest["repo"]["commit_or_snapshot"]}
        manifest["runpod"]["ports"] = ["8000/http", "8000/tcp"]
        manifest["access"]["http_proxy_required"] = True
        manifest["access"]["tcp_ports_required"] = True
        manifest["startup"]["inspection"] = {"hold_after_success_seconds": 180, "http_artifact_server_port": 8000}
        report = bootstrap_requirements_report(manifest)
        self.assertTrue(report["ok"], report)

    def test_bootstrap_requirements_block_private_registry_without_provider_auth(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-04T00:00:00Z",
        }
        manifest["runpod"]["imageName"] = "ghcr.io/example/private-worker:sha256-deadbeef"
        report = bootstrap_requirements_report(manifest)
        self.assertFalse(report["ok"], report)
        self.assertTrue(report["likely_private_registry_image"])
        self.assertTrue(any(issue["path"] == "runpod.containerRegistryAuthId" for issue in report["errors"]))

        manifest["runpod"]["containerRegistryAuthId"] = "registry-auth-id"
        report = bootstrap_requirements_report(manifest)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["registry_auth_declared"])

    def test_registry_auth_plan_blocks_private_image_without_provider_auth(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["runpod"]["imageName"] = "ghcr.io/example/private-worker:sha256-deadbeef"
        plan = build_registry_auth_plan(manifest)
        self.assertFalse(plan["ok"], plan)
        self.assertEqual(plan["status"], "provider_registry_auth_required")
        self.assertTrue(any("runpodctl registry create" in command for command in plan["commands"]))

        manifest["runpod"]["containerRegistryAuthId"] = "registry-auth-id"
        plan = build_registry_auth_plan(manifest)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["status"], "provider_registry_auth_declared")
        self.assertIn("runpodctl registry get registry-auth-id", plan["commands"])

    def test_registry_auth_plan_renders_ecr_refresh(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["runpod"]["imageName"] = "123456789012.dkr.ecr.us-west-2.amazonaws.com/runpod-worker:sha"
        manifest["aws"] = {"ecr": {"runpod_registry_auth_name": "runpod-ecr-smoke"}}
        plan = build_registry_auth_plan(manifest)
        self.assertFalse(plan["ok"], plan)
        self.assertEqual(plan["ecr"]["region"], "us-west-2")
        self.assertTrue(any("aws ecr get-login-password --region us-west-2" in command for command in plan["commands"]))
        self.assertTrue(any("runpodctl registry create --name runpod-ecr-smoke" in command for command in plan["commands"]))

    def test_doctor_points_to_centralized_env_when_api_key_missing(self):
        old_api_key = os.environ.pop("RUNPOD_API_KEY", None)
        old_env = os.environ.get("RUNPOD_BRIDGE_SYMPHONY_ENV")
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "env.sh"
            env_path.write_text("export RUNPOD_API_KEY=redacted\n")
            os.environ["RUNPOD_BRIDGE_SYMPHONY_ENV"] = str(env_path)
            try:
                report = run_doctor()
            finally:
                if old_api_key is not None:
                    os.environ["RUNPOD_API_KEY"] = old_api_key
                if old_env is None:
                    os.environ.pop("RUNPOD_BRIDGE_SYMPHONY_ENV", None)
                else:
                    os.environ["RUNPOD_BRIDGE_SYMPHONY_ENV"] = old_env
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual(checks["symphony_env"]["status"], "pass")
        self.assertIn("source centralized env first", checks["RUNPOD_API_KEY"]["message"])
        self.assertIn(str(env_path), checks["RUNPOD_API_KEY"]["message"])

    def test_plan_infers_small_dry_run(self):
        plan = build_plan(self.manifest())
        self.assertEqual(plan["task_scale"], "small")
        self.assertFalse(plan["remote_ready"])
        self.assertIn("remote_launch_allowed is false; dry-run only", plan["blockers"])
        self.assertEqual(plan["billing"]["cost_center"], "")

    def test_render_startup_contains_monitoring_contract(self):
        script = render_startup_script(self.manifest())
        self.assertIn("RUNPOD_HEARTBEAT_FILE", script)
        self.assertIn("RUNPOD_PROGRESS_SERVER_PORT", script)
        self.assertIn("health_payload", script)
        self.assertIn("RUNPOD_STATUS_FILE", script)
        self.assertIn("HASH_PATH", script)
        self.assertIn('"hash_bytes": hash_bytes', script)
        self.assertIn("artifact_hashes.jsonl", script)
        self.assertIn("replace-with-workload-command", script)
        self.assertIn("RUNPOD_VALIDATION_SCRIPT", script)
        self.assertIn("RUNPOD_REPO_DIR", script)
        self.assertIn("RUNPOD_ENABLE_REPO_BOOTSTRAP", script)
        self.assertIn("RUNPOD_BOOTSTRAP_TIMEOUT_SECONDS", script)
        self.assertIn("bootstrap_fail 92", script)
        self.assertIn("RUNPOD_ARCHIVE_PATH", script)
        self.assertIn("RUNPOD_EGRESS_STATUS_FILE", script)
        self.assertIn("durable_egress", script)
        self.assertIn("RUNPOD_TERMINAL_HOLD_MODE", script)
        self.assertIn("terminal_hold()", script)
        self.assertIn('self.path == "/tail"', script)
        self.assertIn("tail_authorized", script)
        self.assertIn("MAX_LOG_TAIL_BYTES = 12000", script)
        self.assertIn("log tail requires a progress token", script)
        self.assertIn("git checkout", script)
        self.assertIn('cd "${RUNPOD_REPO_DIR:-/workspace/repo}"', script)
        self.assertIn("RUNPOD_LOG_FILE=\"${RUNPOD_LOG_FILE:-runpod-execution/logs/startup.log}\"", script)

    def test_terminal_hold_renders_sleep_infinity(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["startup"]["terminal_hold"] = {
            "mode": "sleep_infinity",
            "on_success": True,
            "on_failure": True,
        }
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.as_dict())
        script = render_startup_script(manifest)
        self.assertIn('RUNPOD_TERMINAL_HOLD_MODE="${RUNPOD_TERMINAL_HOLD_MODE:-sleep_infinity}"', script)
        self.assertIn('event "terminal_hold" "sleep_infinity"', script)
        self.assertIn("sleep infinity", script)
        self.assertIn('if [ "$RUNPOD_TERMINAL_HOLD_RAN" != "1" ]; then', script)

    def test_aws_s3_presigned_upload_egress_mode(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "aws_s3_presigned_upload",
                "archive_upload_url_ref": "env:CUSTOM_ARCHIVE_PUT_URL",
                "hash_upload_url_ref": "env:CUSTOM_HASH_PUT_URL",
                "requires_presigned_upload": True,
            }
        )
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.as_dict())
        plan = build_egress_plan(manifest)
        self.assertTrue(plan["ok"], plan)
        self.assertTrue(plan["durable"])
        self.assertEqual(plan["required_env"], ["CUSTOM_ARCHIVE_PUT_URL", "CUSTOM_HASH_PUT_URL"])
        self.assertTrue(any("$CUSTOM_ARCHIVE_PUT_URL" in command for command in plan["commands"]))
        self.assertTrue(any("$CUSTOM_HASH_PUT_URL" in command for command in plan["commands"]))

        script = render_startup_script(manifest)
        self.assertIn('RUNPOD_EGRESS_MODE="${RUNPOD_EGRESS_MODE:-aws_s3_presigned_upload}"', script)
        self.assertIn('RUNPOD_PRESIGNED_ARCHIVE_URL_ENV="${RUNPOD_PRESIGNED_ARCHIVE_URL_ENV:-CUSTOM_ARCHIVE_PUT_URL}"', script)
        self.assertIn('RUNPOD_PRESIGNED_HASH_URL_ENV="${RUNPOD_PRESIGNED_HASH_URL_ENV:-CUSTOM_HASH_PUT_URL}"', script)
        self.assertIn("presigned_upload_file()", script)
        self.assertIn("curl --fail --silent --show-error --upload-file", script)
        self.assertIn("urllib.request.urlopen", script)
        self.assertIn("curl or python is required for aws_s3_presigned_upload", script)
        self.assertIn('destination="aws-s3-presigned-upload"', script)

    def test_aws_s3_presigned_upload_rejects_literal_url(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "aws_s3_presigned_upload",
                "archive_upload_url": "https://s3.amazonaws.com/public-bucket/object?X-Amz-Signature=redacted",
                "hash_upload_url": "https://s3.amazonaws.com/public-bucket/hash?X-Amz-Signature=redacted",
                "requires_presigned_upload": True,
            }
        )
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("bearer credentials" in issue.message for issue in result.errors))

    def test_object_store_upload_resolves_destination_uri_ref(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "object_store_upload",
                "destination_uri_ref": "env:CUSTOM_OBJECT_STORE_URI",
                "credentials_ref": "aws-sts:role",
                "requires_object_store_upload": True,
            }
        )
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.as_dict())
        script = render_startup_script(manifest)
        self.assertIn('RUNPOD_OBJECT_STORE_URI_ENV="${RUNPOD_OBJECT_STORE_URI_ENV:-CUSTOM_OBJECT_STORE_URI}"', script)
        self.assertIn("resolve_object_store_env", script)

    def test_closeout_hashes_artifacts(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "runpod-execution/artifacts/result.txt",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artifact = base / "runpod-execution" / "artifacts" / "result.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n")
            (base / "runpod-execution" / "logs").mkdir(parents=True, exist_ok=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("done\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (base / "runpod-execution" / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/result.txt"}\n')

            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "succeeded")
            self.assertTrue((base / "runpod-execution" / "closeout.json").is_file())
            self.assertTrue(closeout["artifacts"][0]["sha256"])

    def test_closeout_fails_required_object_store_egress_failure(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "object_store_upload",
                "destination_uri_ref": "env:RUNPOD_OBJECT_STORE_URI",
                "requires_object_store_upload": True,
            }
        )
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "runpod-execution/artifacts/result.txt",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artifact = base / "runpod-execution" / "artifacts" / "result.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n")
            (base / "runpod-execution" / "logs").mkdir(parents=True, exist_ok=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("done\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (base / "runpod-execution" / "egress_status.json").write_text(json.dumps({"mode": "object_store_upload", "status": "failed"}))
            (base / "runpod-execution" / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/result.txt"}\n')

            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "failed")
            self.assertFalse(closeout["egress_ok"])

    def test_closeout_requires_object_store_verification_not_just_upload(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "object_store_upload",
                "destination_uri_ref": "env:RUNPOD_OBJECT_STORE_URI",
                "requires_object_store_upload": True,
            }
        )
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "runpod-execution/artifacts/result.txt",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artifact = base / "runpod-execution" / "artifacts" / "result.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n")
            (base / "runpod-execution" / "logs").mkdir(parents=True, exist_ok=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("done\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (base / "runpod-execution" / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/result.txt"}\n')
            egress_path = base / "runpod-execution" / "egress_status.json"

            egress_path.write_text(json.dumps({"mode": "object_store_upload", "status": "uploaded"}))
            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "failed")
            self.assertFalse(closeout["egress_ok"])

            egress_path.write_text(json.dumps({"mode": "object_store_upload", "status": "verified"}))
            verified = write_closeout_files(manifest, base)
            self.assertEqual(verified["status"], "succeeded")
            self.assertTrue(verified["egress_ok"])

    def test_closeout_requires_downloaded_s3_volume_egress(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update({"mode": "runpod_network_volume_s3"})
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "runpod-execution/artifacts/result.txt",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            execution = base / "runpod-execution"
            artifact = execution / "artifacts" / "result.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n")
            (execution / "logs").mkdir(parents=True, exist_ok=True)
            (execution / "logs" / "startup.log").write_text("done\n")
            (execution / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (execution / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (execution / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/result.txt"}\n')
            (execution / "egress_status.json").write_text(json.dumps({"mode": "runpod_network_volume_s3", "status": "retained"}))

            retained = write_closeout_files(manifest, base)
            self.assertEqual(retained["status"], "failed")
            self.assertFalse(retained["egress_ok"])

            (execution / "egress_status.json").write_text(json.dumps({"mode": "runpod_network_volume_s3", "status": "verified"}))
            verified = write_closeout_files(manifest, base)
            self.assertEqual(verified["status"], "succeeded")
            self.assertTrue(verified["egress_ok"])

    def test_closeout_fails_live_placeholder_artifact_content(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "runpod-execution/artifacts/result.json",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artifact = base / "runpod-execution" / "artifacts" / "result.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text('{"status": "mock output"}\n')
            (base / "runpod-execution" / "logs").mkdir(parents=True, exist_ok=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("done\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (base / "runpod-execution" / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/result.json"}\n')

            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "failed")
            self.assertEqual(closeout["forbidden_artifact_markers"][0]["markers"], ["mock"])

    def test_closeout_requires_declared_log_heartbeat_and_hash_ledger(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "result",
                "path": "/workspace/runpod-execution/artifacts/result.txt",
                "required": True,
                "sha256_required": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artifact = base / "runpod-execution" / "artifacts" / "result.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded"}))
            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "failed")
            self.assertTrue(any("log artifact" in item for item in closeout["missing_required_evidence"]))
            self.assertTrue(any("workload heartbeat" in item for item in closeout["missing_required_evidence"]))
            self.assertTrue(any("artifact hash ledger" in item for item in closeout["missing_required_evidence"]))

    def test_monitor_reads_local_execution_packet(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            (base / "runpod-execution" / "logs").mkdir(parents=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("running\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "running", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text(
                json.dumps({"ts": ts, "phase": "monitor", "status": "alive", "source": "cloud_bridge"}) + "\n"
            )

            previous = {
                "files": {"log_bytes": 0, "hash_bytes": 0},
                "status": {"status": "running", "exit_code": 0},
                "last_heartbeat": {"ts": ts, "phase": "monitor", "status": "alive", "source": "cloud_bridge"},
            }
            report = inspect_execution(manifest, base, previous_report=previous)
            self.assertEqual(report["state"], "running")
            self.assertTrue(report["files"]["log_present"])
            self.assertEqual(report["productivity"]["state"], "workload_progressing")
            self.assertTrue(report["productivity"]["productive"])

    def test_monitor_heartbeat_alone_is_not_productivity(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            (base / "runpod-execution" / "logs").mkdir(parents=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "running", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text(
                json.dumps({"ts": ts, "phase": "monitor", "status": "alive", "source": "cloud_bridge"}) + "\n"
            )
            report = inspect_execution(manifest, base)
            self.assertEqual(report["productivity"]["state"], "harness_alive_unproven")
            self.assertFalse(report["productivity"]["productive"])

    def test_monitor_terminal_status_is_not_final_success(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runpod-execution").mkdir()
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "succeeded"}))
            report = inspect_execution(manifest, base)
            self.assertEqual(report["state"], "terminal_reported")
            self.assertTrue(report["workload_terminal_reported"])
            self.assertEqual(report["workload_terminal_status"], "succeeded")
            self.assertFalse(report["final_success"])

    def test_monitor_rejects_future_heartbeat_timestamp(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            (base / "runpod-execution").mkdir(parents=True)
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "running", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text(
                json.dumps({"ts": future, "phase": "monitor", "status": "alive", "source": "cloud_bridge"}) + "\n"
            )
            report = inspect_execution(manifest, base)
            self.assertEqual(report["state"], "silent_timeout")
            self.assertTrue(report["silence"]["invalid_timestamp"])

    def test_monitor_times_out_running_without_required_heartbeat(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runpod-execution").mkdir()
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "running"}))
            report = inspect_execution(manifest, base)
            self.assertEqual(report["state"], "silent_timeout")
            self.assertEqual(report["silence"]["reason"], "required heartbeat is missing or unparseable")

    def test_run_local_executes_contract(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = run_local(manifest, repo_dir=base / "repo", runtime_dir=base / "runtime")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["closeout"]["status"], "succeeded")
            self.assertTrue((base / "repo" / "runpod-execution" / "artifacts" / "cheap-pod-result.json").is_file())
            self.assertTrue((base / "repo" / "runpod-execution" / "artifacts" / "runpod-execution.tar.gz").is_file())

    def test_productivity_plan_distinguishes_progress_from_completion_probe(self):
        manifest = load_manifest(ROOT / "examples" / "proxy-matrix" / "launch_manifest.json")
        result = validate_manifest(manifest)
        self.assertTrue(result.ok, result.as_dict())
        plan = build_productivity_plan(manifest, pod_id="pod-123", public_ip="127.0.0.1", external_port=54311)
        self.assertTrue(plan["ok"], plan)
        self.assertTrue(plan["has_live_productivity_channel"], plan)
        signal_status = {signal["name"]: signal["status"] for signal in plan["signals"]}
        self.assertEqual(signal_status["live_progress_http"], "configured")
        self.assertEqual(signal_status["artifact_inspection_http"], "completion_only")
        self.assertEqual(signal_status["runpod_graphql_runtime"], "read_only_probe")
        self.assertTrue(any("/healthz" in command for command in plan["commands"]))
        self.assertTrue(any("runtime-metrics pod-123" in command for command in plan["commands"]))
        self.assertTrue(any("connection_refused_means" in signal for signal in plan["signals"]))

    def test_progress_report_keeps_monitor_liveness_separate(self):
        live_payload = {
            "monitor_alive": True,
            "status": {"status": "running", "phase": "startup"},
            "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
            "files": {"log_bytes": 10},
        }
        previous = {
            "live_progress": {
                "payload": {
                    "monitor_alive": True,
                    "status": {"status": "running", "phase": "startup"},
                    "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
                    "files": {"log_bytes": 10},
                }
            }
        }
        classification = classify_progress(
            {"ok": True},
            {"ok": True, "analysis": {"state": "runtime_alive", "evidence": [], "warnings": []}},
            {"ok": True, "payload": live_payload},
            previous,
        )
        self.assertEqual(classification["state"], "harness_alive_progress_unproven")
        self.assertTrue(classification["monitor_alive"])
        self.assertFalse(classification["workload_progressing"])

    def test_progress_report_marks_log_growth_as_workload_progress(self):
        live_payload = {
            "monitor_alive": True,
            "status": {"status": "running", "phase": "startup"},
            "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
            "files": {"log_bytes": 25},
        }
        previous = {
            "live_progress": {
                "payload": {
                    "monitor_alive": True,
                    "status": {"status": "running", "phase": "startup"},
                    "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
                    "files": {"log_bytes": 10},
                }
            }
        }
        classification = classify_progress(
            {"ok": True},
            {"ok": True, "analysis": {"state": "runtime_alive", "evidence": [], "warnings": []}},
            {"ok": True, "payload": live_payload},
            previous,
        )
        self.assertEqual(classification["state"], "workload_progressing")
        self.assertTrue(classification["workload_progressing"])

    def test_progress_report_marks_hash_ledger_growth_as_workload_progress(self):
        live_payload = {
            "monitor_alive": True,
            "status": {"status": "running", "phase": "startup"},
            "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
            "files": {"log_bytes": 10, "hash_bytes": 48},
        }
        previous = {
            "live_progress": {
                "payload": {
                    "monitor_alive": True,
                    "status": {"status": "running", "phase": "startup"},
                    "last_heartbeat": {"phase": "monitor", "status": "alive", "source": "cloud_bridge"},
                    "files": {"log_bytes": 10, "hash_bytes": 0},
                }
            }
        }
        classification = classify_progress(
            {"ok": True},
            {"ok": True, "analysis": {"state": "runtime_alive", "evidence": [], "warnings": []}},
            {"ok": True, "payload": live_payload},
            previous,
        )
        self.assertEqual(classification["state"], "workload_progressing")
        self.assertIn("hash_ledger_grew:0->48", classification["evidence"])

    def test_fetch_live_progress_attaches_auth_log_tail(self):
        manifest = self.manifest()
        manifest["runpod"]["ports"] = ["8000/http"]
        manifest["startup"]["progress"] = {
            "http_status_server_port": 8000,
            "auth_token_ref": "env:RUNPOD_PROGRESS_TOKEN",
            "include_log_tail": True,
            "log_tail_bytes": 16_000,
        }
        previous_token = os.environ.get("RUNPOD_PROGRESS_TOKEN")
        os.environ["RUNPOD_PROGRESS_TOKEN"] = "progress-secret-token"
        seen: dict[str, object] = {}

        def fake_fetch_json_url(url, *, timeout_seconds, headers=None):
            seen["json_url"] = url
            seen["json_headers"] = headers
            return {
                "ok": True,
                "status": "ok",
                "url": url,
                "payload": {"monitor_alive": True, "files": {"log_bytes": 123}},
            }

        def fake_fetch_text_url(url, *, timeout_seconds, headers=None, max_bytes, redact_tokens=None):
            seen["tail_url"] = url
            seen["tail_headers"] = headers
            seen["tail_max_bytes"] = max_bytes
            seen["redact_tokens"] = redact_tokens
            return {
                "ok": True,
                "status": "ok",
                "url": url,
                "bytes": 16,
                "truncated": False,
                "text": redact_sensitive_text(
                    ("HF_" + "TOK" + "EN=hf_1234567890abcdef\nAuthorization: " + "Bear" + "er progress-secret-token"),
                    redact_tokens or [],
                ),
            }

        original_json = progress_report_module.fetch_json_url
        original_text = progress_report_module.fetch_text_url
        progress_report_module.fetch_json_url = fake_fetch_json_url
        progress_report_module.fetch_text_url = fake_fetch_text_url
        try:
            report = fetch_live_progress(
                manifest,
                "pod-123",
                pod={},
                mode="auto",
                public_ip="",
                external_port=None,
                timeout_seconds=3,
            )
        finally:
            progress_report_module.fetch_json_url = original_json
            progress_report_module.fetch_text_url = original_text
            if previous_token is None:
                os.environ.pop("RUNPOD_PROGRESS_TOKEN", None)
            else:
                os.environ["RUNPOD_PROGRESS_TOKEN"] = previous_token

        self.assertTrue(report["ok"], report)
        self.assertIn("/healthz", seen["json_url"])
        self.assertIn("/tail", seen["tail_url"])
        self.assertEqual(seen["tail_max_bytes"], 12_000)
        self.assertEqual(seen["json_headers"]["X-Runpod-Progress-Token"], "progress-secret-token")
        self.assertEqual(seen["tail_headers"]["Authorization"], "Bear" + "er progress-secret-token")
        self.assertEqual(seen["redact_tokens"], ["progress-secret-token"])
        self.assertIn("log_tail", report)
        self.assertNotIn("progress-secret-token", report["log_tail"]["text"])
        self.assertNotIn("hf_1234567890abcdef", report["log_tail"]["text"])

    def test_fetch_live_progress_skips_log_tail_without_auth_token(self):
        manifest = self.manifest()
        manifest["runpod"]["ports"] = ["8000/http"]
        manifest["startup"]["progress"] = {
            "http_status_server_port": 8000,
            "auth_token_ref": "env:RUNPOD_PROGRESS_TOKEN",
            "include_log_tail": True,
            "log_tail_bytes": 4096,
        }
        previous_token = os.environ.pop("RUNPOD_PROGRESS_TOKEN", None)
        seen = {"tail_called": False}

        def fake_fetch_json_url(url, *, timeout_seconds, headers=None):
            return {"ok": True, "status": "ok", "url": url, "payload": {"monitor_alive": True}}

        def fake_fetch_text_url(*args, **kwargs):
            seen["tail_called"] = True
            return {"ok": True, "text": "should not happen"}

        original_json = progress_report_module.fetch_json_url
        original_text = progress_report_module.fetch_text_url
        progress_report_module.fetch_json_url = fake_fetch_json_url
        progress_report_module.fetch_text_url = fake_fetch_text_url
        try:
            report = fetch_live_progress(
                manifest,
                "pod-123",
                pod={},
                mode="auto",
                public_ip="",
                external_port=None,
                timeout_seconds=3,
            )
        finally:
            progress_report_module.fetch_json_url = original_json
            progress_report_module.fetch_text_url = original_text
            if previous_token is not None:
                os.environ["RUNPOD_PROGRESS_TOKEN"] = previous_token

        self.assertTrue(report["ok"], report)
        self.assertFalse(seen["tail_called"])
        self.assertEqual(report["log_tail"]["status"], "auth_token_unavailable")

    def test_progress_report_tail_failure_does_not_invalidate_healthz(self):
        manifest = self.manifest()
        manifest["runpod"]["ports"] = ["8000/http"]
        manifest["startup"]["progress"] = {
            "http_status_server_port": 8000,
            "auth_token_ref": "env:RUNPOD_PROGRESS_TOKEN",
            "include_log_tail": True,
        }
        previous_token = os.environ.get("RUNPOD_PROGRESS_TOKEN")
        os.environ["RUNPOD_PROGRESS_TOKEN"] = "progress-secret-token"

        def fake_fetch_json_url(url, *, timeout_seconds, headers=None):
            return {"ok": True, "status": "ok", "url": url, "payload": {"monitor_alive": True}}

        def fake_fetch_text_url(url, *, timeout_seconds, headers=None, max_bytes, redact_tokens=None):
            return {"ok": False, "status": "http_error", "url": url, "bytes": 0, "text": "", "error": "HTTP 401"}

        original_json = progress_report_module.fetch_json_url
        original_text = progress_report_module.fetch_text_url
        progress_report_module.fetch_json_url = fake_fetch_json_url
        progress_report_module.fetch_text_url = fake_fetch_text_url
        try:
            report = fetch_live_progress(
                manifest,
                "pod-123",
                pod={},
                mode="auto",
                public_ip="",
                external_port=None,
                timeout_seconds=3,
            )
        finally:
            progress_report_module.fetch_json_url = original_json
            progress_report_module.fetch_text_url = original_text
            if previous_token is None:
                os.environ.pop("RUNPOD_PROGRESS_TOKEN", None)
            else:
                os.environ["RUNPOD_PROGRESS_TOKEN"] = previous_token

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["log_tail"]["status"], "http_error")
        self.assertEqual(report["log_tail"]["error"], "HTTP 401")

    def test_fetch_live_progress_uses_runtime_tcp_mapping_when_rest_mapping_lags(self):
        manifest = self.manifest()
        manifest["runpod"]["ports"] = ["8000/tcp"]
        manifest["startup"]["progress"] = {
            "http_status_server_port": 8000,
        }
        runtime = {
            "metrics": {
                "ports": [
                    {"ip": "203.0.113.20", "privatePort": 8000, "publicPort": 31800, "type": "tcp"},
                ]
            }
        }
        seen: dict[str, object] = {}

        def fake_fetch_json_url(url, *, timeout_seconds, headers=None):
            seen["url"] = url
            return {"ok": True, "status": "ok", "url": url, "payload": {"monitor_alive": True}}

        original_json = progress_report_module.fetch_json_url
        progress_report_module.fetch_json_url = fake_fetch_json_url
        try:
            report = fetch_live_progress(
                manifest,
                "pod-123",
                pod={"id": "pod-123", "publicIp": "", "portMappings": None},
                runtime=runtime,
                mode="auto",
                public_ip="",
                external_port=None,
                timeout_seconds=3,
            )
        finally:
            progress_report_module.fetch_json_url = original_json

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["mode"], "tcp")
        self.assertEqual(seen["url"], "http://203.0.113.20:31800/healthz")

    def test_progress_report_http_404_recommends_artifact_fetch_before_cleanup(self):
        classification = classify_progress(
            {"ok": True},
            {"ok": False, "analysis": {"state": "", "evidence": [], "warnings": []}},
            {
                "ok": False,
                "status": "unreachable",
                "error": "HTTP 404: ",
                "attempts": [{"ok": False, "error": "HTTP 404: "}],
                "payload": {},
            },
            None,
        )
        self.assertEqual(classification["state"], "provider_alive_workload_unproven")
        self.assertEqual(classification["next_action"], "try_declared_artifact_paths_before_cleanup")
        self.assertTrue(any("Try declared artifact paths" in warning for warning in classification["warnings"]))

    def test_cli_progress_report_prints_classification_keys_for_agents(self):
        def fake_build_progress_report(*args, **kwargs):
            return {
                "classification": {
                    "state": "harness_alive_progress_unproven",
                    "workload_progressing": False,
                    "monitor_alive": True,
                    "outage_suspected": False,
                    "cleanup_recommended": False,
                    "next_action": "continue_monitoring_with_previous_progress_report",
                    "evidence": [],
                    "warnings": [],
                }
            }

        original = cli_module.build_progress_report
        cli_module.build_progress_report = fake_build_progress_report
        try:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["progress-report", str(TEMPLATE), "pod-123"])
        finally:
            cli_module.build_progress_report = original
        self.assertEqual(rc, 0)
        text = stdout.getvalue()
        self.assertIn("classification.state: harness_alive_progress_unproven", text)
        self.assertIn("classification.workload_progressing: false", text)
        self.assertIn("classification.monitor_alive: true", text)

    def test_persist_progress_snapshot_writes_latest_and_jsonl_with_previous(self):
        manifest = self.manifest()
        seen_previous = []

        def fake_build_progress_report(manifest_arg, pod_id, *, previous_report=None, client=None, mode="auto", progress_timeout_seconds=3):
            seen_previous.append(previous_report)
            return {
                "pod_id": pod_id,
                "sequence": len(seen_previous),
                "classification": {
                    "state": "workload_progressing",
                    "workload_progressing": True,
                    "monitor_alive": True,
                    "outage_suspected": False,
                    "next_action": "continue_monitoring_with_previous_progress_report",
                },
            }

        original = remote_run_module.build_progress_report
        remote_run_module.build_progress_report = fake_build_progress_report
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                first = remote_run_module.persist_progress_snapshot(manifest, "pod-123", object(), base)
                second = remote_run_module.persist_progress_snapshot(manifest, "pod-123", object(), base)
                latest = json.loads((base / "remote_progress_latest.json").read_text())
                history = [json.loads(line) for line in (base / "remote_progress.jsonl").read_text().splitlines()]
        finally:
            remote_run_module.build_progress_report = original

        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(latest["sequence"], 2)
        self.assertEqual(len(history), 2)
        self.assertEqual(seen_previous[0], {})
        self.assertEqual(seen_previous[1]["sequence"], 1)

    def test_persist_progress_snapshot_records_progress_report_error(self):
        manifest = self.manifest()

        def failing_build_progress_report(*args, **kwargs):
            raise RuntimeError("network unavailable")

        original = remote_run_module.build_progress_report
        remote_run_module.build_progress_report = failing_build_progress_report
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = remote_run_module.persist_progress_snapshot(manifest, "pod-123", object(), tmp)
                saved = json.loads((Path(tmp) / "remote_progress_latest.json").read_text())
        finally:
            remote_run_module.build_progress_report = original

        self.assertEqual(report["classification"]["state"], "progress_report_error")
        self.assertEqual(saved["classification"]["state"], "progress_report_error")
        self.assertTrue(any("RuntimeError" in item for item in report["classification"]["warnings"]))

    def test_runtime_metrics_detects_crash_loop_from_tiny_uptime(self):
        metrics = {
            "runtime_present": True,
            "desiredStatus": "RUNNING",
            "uptimeInSeconds": 1,
            "container": {"cpuPercent": 7, "memoryPercent": 0},
            "gpus": [{"gpuUtilPercent": 0, "memoryUtilPercent": 0}],
        }
        analysis = analyze_runtime_metrics(metrics, expected_elapsed_seconds=38 * 60)
        self.assertEqual(analysis["state"], "crash_loop_suspected")
        self.assertTrue(analysis["crash_loop_suspected"])
        self.assertEqual(analysis["activity_sample"], "nonzero")
        self.assertTrue(any("expected elapsed time" in item for item in analysis["evidence"]))
        self.assertIsNone(analysis["productive"])

    def test_runtime_metrics_detects_uptime_reset_between_samples(self):
        metrics = {
            "runtime_present": True,
            "desiredStatus": "RUNNING",
            "uptimeInSeconds": 4,
            "container": {"cpuPercent": 0, "memoryPercent": 0},
            "gpus": [],
        }
        previous = {"uptimeInSeconds": 300}
        analysis = analyze_runtime_metrics(metrics, previous_metrics=previous)
        self.assertEqual(analysis["state"], "crash_loop_suspected")
        self.assertTrue(any("dropped" in item for item in analysis["evidence"]))

    def test_runtime_metrics_flags_negative_uptime_as_invalid_telemetry(self):
        metrics = {
            "runtime_present": True,
            "desiredStatus": "RUNNING",
            "uptimeInSeconds": -4,
            "container": {"cpuPercent": 0, "memoryPercent": 0},
            "gpus": [],
        }
        analysis = analyze_runtime_metrics(metrics, previous_metrics={"uptimeInSeconds": 1})
        self.assertEqual(analysis["state"], "invalid_runtime_telemetry")
        self.assertFalse(analysis["runtime_alive"])
        self.assertFalse(analysis["crash_loop_suspected"])
        self.assertTrue(any("negative" in item for item in analysis["warnings"]))

    def test_runtime_metrics_report_uses_graphql_client_shape(self):
        class FakeRuntimeClient:
            def pod_runtime(self, pod_id):
                return {
                    "id": pod_id,
                    "name": "symphony-test",
                    "desiredStatus": "RUNNING",
                    "runtime": {
                        "uptimeInSeconds": 3931,
                        "container": {"cpuPercent": 0, "memoryPercent": 2},
                        "gpus": [{"id": "GPU-1", "gpuUtilPercent": 12, "memoryUtilPercent": 4}],
                        "ports": [{"privatePort": 8000, "publicPort": 58000, "type": "http"}],
                    },
                }

        report = build_runtime_metrics_report("pod-123", client=FakeRuntimeClient())
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["analysis"]["state"], "runtime_alive")
        self.assertEqual(report["metrics"]["gpus"][0]["gpuUtilPercent"], 12)

    def test_gpu_catalog_report_separates_catalog_mismatch_from_capacity(self):
        class FakeCatalogClient:
            def request(self, query, variables=None):
                return {
                    "data": {
                        "gpuTypes": [
                            {
                                "id": "NVIDIA A100-SXM4-80GB",
                                "displayName": "A100 SXM4 80GB",
                                "nodeGroupDatacenters": [
                                    {
                                        "id": "US-KS-2",
                                        "name": "US-KS-2",
                                        "gpuAvailability": {"available": True, "stockStatus": "High"},
                                    }
                                ],
                            },
                            {
                                "id": "NVIDIA L40",
                                "displayName": "L40",
                                "nodeGroupDatacenters": [],
                            },
                        ]
                    }
                }

        report = build_gpu_catalog_report(
            gpu_type_ids=["NVIDIA L40", "NVIDIA A100-SXM4-80GB"],
            data_center_ids=["US-KS-2"],
            cloud_type="SECURE",
            client=FakeCatalogClient(),
        )
        self.assertTrue(report["constraints_satisfied"], report)
        self.assertEqual(report["summary"]["available_requested_combo_count"], 1)
        self.assertTrue(any(match["reason"] == "gpu_type_not_offered_in_requested_data_center" for match in report["catalog_matches"]))

    def test_gpu_catalog_report_blocks_when_no_requested_combo_is_offered(self):
        class FakeCatalogClient:
            def request(self, query, variables=None):
                return {
                    "data": {
                        "gpuTypes": [
                            {
                                "id": "NVIDIA L40",
                                "displayName": "L40",
                                "nodeGroupDatacenters": [],
                            }
                        ]
                    }
                }

        report = build_gpu_catalog_report(
            gpu_type_ids=["NVIDIA L40"],
            data_center_ids=["US-KS-2"],
            cloud_type="SECURE",
            client=FakeCatalogClient(),
        )
        self.assertFalse(report["constraints_satisfied"], report)
        self.assertTrue(any("Do not retry REST create" in item for item in report["recommendations"]))

    def test_prepare_packet_writes_preflight_and_startup(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            result = prepare_packet(manifest, tmp)
            base = Path(tmp)
            self.assertTrue(result["ok"])
            self.assertTrue((base / "launch_manifest.json").is_file())
            self.assertTrue((base / "local_preflight.json").is_file())
            self.assertTrue((base / "startup.sh").is_file())
            self.assertTrue((base / "provider_handoff.json").is_file())
            self.assertIn("remote_launch_allowed is false; dry-run only", result["plan"]["blockers"])
            handoff = json.loads((base / "provider_handoff.json").read_text())
            self.assertEqual(handoff["status"], "blocked")
            self.assertEqual(handoff["remote_execution_by"], "orchestrator")

    def test_prepare_packet_packages_local_snapshot(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["repo"]["source"] = "local_snapshot"
        manifest["repo"]["url_or_path"] = "."
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "src"
            source_dir.mkdir()
            (source_dir / "run.py").write_text("print('ok')\n")
            out = Path(tmp) / "packet"
            result = prepare_packet(
                manifest,
                out,
                source_dir=source_dir,
                source_archive_pod_path="/workspace/.runpod-bridge/source_snapshot.tar.gz",
            )
            packet_manifest = load_manifest(out / "launch_manifest.json")
            self.assertEqual(packet_manifest["repo"]["source"], "prepared_snapshot")
            self.assertTrue(packet_manifest["repo"]["commit_or_snapshot"].startswith("sha256:"))
            self.assertEqual(packet_manifest["repo"]["snapshot"]["archive_pod_path"], "/workspace/.runpod-bridge/source_snapshot.tar.gz")
            self.assertTrue(Path(result["files"]["source_archive"]).is_file())

    def test_source_archive_excludes_secret_and_dependency_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "app.py").write_text("print('ok')\n")
            example_home_path = "/" + "Users/example/private/project"
            (source / "notes.md").write_text(f"local path: {example_home_path}\n")
            (source / ".env").write_text("TO" + "KEN=" + "secret\n")
            (source / "deploy.pem").write_text("private key\n")
            (source / ".config" / "gcloud").mkdir(parents=True)
            (source / ".config" / "gcloud" / "application_default_credentials.json").write_text("{}\n")
            (source / "node_modules").mkdir()
            (source / "node_modules" / "pkg.js").write_text("ignored\n")
            out = Path(tmp) / "packet"
            manifest = prepare_source_archive(source, out)
            import tarfile

            with tarfile.open(manifest["archive_path"], "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("app.py", names)
            self.assertNotIn(".env", names)
            self.assertNotIn("deploy.pem", names)
            self.assertNotIn(".config/gcloud/application_default_credentials.json", names)
            self.assertNotIn("node_modules/pkg.js", names)
            self.assertEqual(manifest["personal_path_matches"], [{"path": "notes.md", "line": 1}])
            self.assertTrue(manifest["warnings"])

    def test_prepared_snapshot_requires_archive_ref_for_remote(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-15T00:00:00Z",
        }
        manifest["repo"] = {
            "source": "prepared_snapshot",
            "url_or_path": "prepared_snapshot",
            "commit_or_snapshot": "sha256:" + "a" * 64,
            "workdir": "/workspace/repo",
            "snapshot": {"archive_sha256": "a" * 64},
        }
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "repo.snapshot.archive_url_ref" for issue in result.errors))

    def test_remote_required_artifacts_require_sha256(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-15T00:00:00Z",
        }
        manifest["expected_artifacts"][0].pop("sha256_required", None)
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "expected_artifacts[0].sha256_required" for issue in result.errors))

    def test_prepared_snapshot_can_use_network_volume_archive_path(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-15T00:00:00Z",
        }
        manifest["repo"] = {
            "source": "prepared_snapshot",
            "url_or_path": "prepared_snapshot",
            "commit_or_snapshot": "sha256:" + "b" * 64,
            "workdir": "/workspace/repo",
            "snapshot": {
                "archive_sha256": "b" * 64,
                "archive_pod_path": "/workspace/.runpod-bridge/source_snapshot.tar.gz",
            },
        }
        manifest["runpod"]["networkVolumeId"] = "vol-private-source"
        manifest["runpod"]["dataCenterIds"] = ["US-KS-2"]
        validation = validate_manifest(manifest)
        self.assertTrue(validation.ok, validation.errors)
        plan = build_source_ingress_plan(manifest, source_archive_path=".runtime/source_snapshot.tar.gz")
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["mode"], "runpod_network_volume_snapshot")
        self.assertIn("AWS_ACCESS_KEY_ID", plan["required_env"])
        self.assertTrue(any("s3api-us-ks-2.runpod.io" in command for command in plan["commands"]))
        script = render_startup_script(manifest)
        self.assertIn("/workspace/.runpod-bridge/source_snapshot.tar.gz", script)
        self.assertIn("using prepared source archive at", script)

    def test_source_ingress_blocks_mismatched_snapshot_archive(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source_snapshot.tar.gz"
            archive.write_bytes(b"private source archive")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest["repo"] = {
                "source": "prepared_snapshot",
                "url_or_path": "prepared_snapshot",
                "commit_or_snapshot": "sha256:" + digest,
                "workdir": "/workspace/repo",
                "snapshot": {
                    "archive_sha256": "0" * 64,
                    "archive_pod_path": "/workspace/.runpod-bridge/source_snapshot.tar.gz",
                },
            }
            manifest["runpod"]["networkVolumeId"] = "vol-private-source"
            manifest["runpod"]["dataCenterIds"] = ["US-KS-2"]
            plan = build_source_ingress_plan(manifest, source_archive_path=archive)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["source_archive_sha256"], digest)
        self.assertTrue(any("SHA-256" in blocker for blocker in plan["blockers"]))

    def test_remote_git_launch_requires_source_proof(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-15T00:00:00Z",
        }
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        manifest["runpod"]["image_capabilities"] = ["git"]
        manifest["runpod"]["ports"] = ["8000/http", "8000/tcp"]
        manifest["access"]["http_proxy_required"] = True
        manifest["access"]["tcp_ports_required"] = True
        manifest["startup"]["inspection"] = {"hold_after_success_seconds": 180, "http_artifact_server_port": 8000}
        preview = build_remote_launch_preview(manifest)
        self.assertFalse(preview["remote_ready"])
        self.assertTrue(preview["source_gate"]["required"])
        manifest["repo"]["source_proof"] = {
            "status": "reachable",
            "url": manifest["repo"]["url_or_path"],
            "ref": manifest["repo"]["commit_or_snapshot"],
        }
        preview = build_remote_launch_preview(manifest)
        self.assertTrue(preview["remote_ready"], preview["plan"]["blockers"])

    def test_build_create_pod_request_infers_cpu_and_bootstrap_env(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        body = build_create_pod_request(manifest)
        self.assertEqual(body["computeType"], "CPU")
        self.assertNotIn("gpuCount", body)
        self.assertEqual(body["env"]["RUNPOD_ENABLE_REPO_BOOTSTRAP"], "1")
        self.assertEqual(body["env"]["RUNPOD_REPO_REF"], "dryrun-snapshot")
        self.assertEqual(body["env"]["RUNPOD_TERMINATE_AFTER_MINUTES"], "15")
        self.assertEqual(body["dockerStartCmd"][:2], ["bash", "-lc"])
        self.assertIn("RUNPOD_VALIDATION_SCRIPT", body["dockerStartCmd"][2])

    def test_payload_report_blocks_oversized_inline_startup(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-03T00:00:00Z",
        }
        manifest["startup"]["commands"].append("python3 - <<'PY'\n" + ("print('x')\n" * 7000) + "PY")
        body = build_create_pod_request(manifest)
        report = create_request_payload_report(body)
        self.assertGreater(report["post_body_bytes"], MAX_POST_BODY_BYTES)
        self.assertFalse(report["ok"])

        preflight = analyze_preflight(manifest)
        self.assertFalse(preflight["ok"])
        self.assertTrue(any(issue["path"] == "runpod.create_request" for issue in preflight["errors"]))

    def test_render_runpodctl_create_includes_terminate_backstop(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        command = build_pod_create_command(manifest)
        self.assertIn("--terminate-after", command)
        self.assertIn("15m", command)
        self.assertIn("--image", command)
        self.assertIn("--docker-args", command)
        self.assertTrue(any("RUNPOD_VALIDATION_SCRIPT" in item for item in command))
        self.assertTrue(shell_join(command).startswith("runpodctl pod create"))

    def test_runpodctl_billing_command_maps_query(self):
        command = billing_pods_command(
            {
                "podId": "pod-123",
                "startTime": "2026-05-01T00:00:00Z",
                "endTime": "2026-05-02T00:00:00Z",
                "bucketSize": "hour",
                "grouping": "podId",
                "gpuId": "NVIDIA A40",
            }
        )
        self.assertEqual(command[:2], ["billing", "pods"])
        self.assertIn("--pod-id", command)
        self.assertIn("pod-123", command)
        self.assertIn("--gpu-id", command)

    def test_public_smoke_disables_repo_bootstrap(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        result = validate_manifest(manifest)
        self.assertTrue(result.ok)
        body = build_create_pod_request(manifest)
        self.assertEqual(body["env"]["RUNPOD_ENABLE_REPO_BOOTSTRAP"], "0")

    def test_proxy_matrix_manifest_declares_proxy_packet_paths(self):
        manifest = load_manifest(ROOT / "examples" / "proxy-matrix" / "launch_manifest.json")
        result = validate_manifest(manifest)
        self.assertTrue(result.ok)
        self.assertTrue(any(issue.path == "access.public_services_require_auth" for issue in result.warnings))
        paths = required_proxy_paths(manifest)
        self.assertIn("runpod-execution/status.json", paths)
        self.assertIn("runpod-execution/artifacts/matrix-summary.json", paths)
        self.assertIn("runpod-execution/artifacts/runpod-execution.tar.gz", paths)
        self.assertEqual(
            proxy_url("pod123", 8000, "runpod-execution/status.json"),
            "https://pod123-8000.proxy.runpod.net/runpod-execution/status.json",
        )

    def test_proxy_packet_extracts_workspace_archive_before_closeout(self):
        manifest = self.manifest()
        manifest["startup"]["status_file"] = "runpod-execution/status.json"
        manifest["startup"]["heartbeat_file"] = "runpod-execution/monitor_events.ndjson"
        manifest["startup"]["log_file"] = "runpod-execution/logs/startup.log"
        manifest["artifact_egress"] = {
            "mode": "workspace_archive",
            "archive_path": "runpod-execution/artifacts/runpod-execution.tar.gz",
        }
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "summary",
                "path": "runpod-execution/artifacts/summary.json",
                "required": True,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "staged"
            staged_paths = {
                "runpod-execution/status.json": '{"status":"succeeded","exit_code":0}\n',
                "runpod-execution/monitor_events.ndjson": '{"ts":"2026-05-27T00:00:00Z","status":"alive"}\n',
                "runpod-execution/logs/startup.log": "done\n",
                "runpod-execution/artifact_hashes.jsonl": "",
                "runpod-execution/artifacts/summary.json": '{"ok":true}\n',
            }
            for rel_path, content in staged_paths.items():
                target = staged / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            archive_path = root / "archive.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for rel_path in staged_paths:
                    archive.add(staged / rel_path, arcname=rel_path)

            original_fetch = proxy_module.fetch_proxy_file

            def fake_fetch(pod_id, port, remote_path, output_path, timeout_seconds=30):
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                if remote_path == "runpod-execution/status.json":
                    output.write_text(staged_paths[remote_path])
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/artifacts/runpod-execution.tar.gz":
                    output.write_bytes(archive_path.read_bytes())
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                return {"ok": False, "url": "test", "output_path": str(output), "error": "not found"}

            proxy_module.fetch_proxy_file = fake_fetch
            try:
                result = proxy_module.verify_proxy_packet(
                    manifest,
                    "pod123",
                    port=8001,
                    out_dir=root / "out",
                    timeout_seconds=1,
                    interval_seconds=0,
                )
            finally:
                proxy_module.fetch_proxy_file = original_fetch

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["archive_materialization"]["ok"], result)
            self.assertTrue((root / "out" / "runpod-execution" / "artifacts" / "summary.json").is_file())
            self.assertEqual(result["closeout"]["status"], "succeeded")

    def test_proxy_packet_archive_does_not_overwrite_fetched_status(self):
        manifest = self.manifest()
        manifest["startup"]["status_file"] = "runpod-execution/status.json"
        manifest["startup"]["heartbeat_file"] = "runpod-execution/monitor_events.ndjson"
        manifest["startup"]["log_file"] = "runpod-execution/logs/startup.log"
        manifest["artifact_egress"] = {
            "mode": "workspace_archive",
            "archive_path": "runpod-execution/artifacts/runpod-execution.tar.gz",
        }
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "summary",
                "path": "runpod-execution/artifacts/summary.json",
                "required": True,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "staged"
            archive_paths = {
                "runpod-execution/status.json": '{"status":"running","phase":"workload_succeeded","exit_code":0}\n',
                "runpod-execution/monitor_events.ndjson": '{"ts":"2026-05-27T00:00:00Z","status":"alive"}\n',
                "runpod-execution/logs/startup.log": "done\n",
                "runpod-execution/artifact_hashes.jsonl": "",
                "runpod-execution/artifacts/summary.json": '{"ok":true}\n',
            }
            for rel_path, content in archive_paths.items():
                target = staged / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            archive_path = root / "archive.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for rel_path in archive_paths:
                    archive.add(staged / rel_path, arcname=rel_path)

            original_fetch = proxy_module.fetch_proxy_file

            def fake_fetch(pod_id, port, remote_path, output_path, timeout_seconds=30):
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                if remote_path == "runpod-execution/status.json":
                    output.write_text('{"status":"succeeded","phase":"complete","exit_code":0}\n')
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/artifacts/runpod-execution.tar.gz":
                    output.write_bytes(archive_path.read_bytes())
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                return {"ok": False, "url": "test", "output_path": str(output), "error": "not found"}

            proxy_module.fetch_proxy_file = fake_fetch
            try:
                result = proxy_module.verify_proxy_packet(
                    manifest,
                    "pod123",
                    port=8001,
                    out_dir=root / "out",
                    timeout_seconds=1,
                    interval_seconds=0,
                )
            finally:
                proxy_module.fetch_proxy_file = original_fetch

            status = json.loads((root / "out" / "runpod-execution" / "status.json").read_text())
            self.assertTrue(result["ok"], result)
            self.assertEqual(status["status"], "succeeded")
            self.assertIn("runpod-execution/status.json", result["archive_materialization"]["skipped_members"])

    def test_proxy_packet_fails_when_workspace_archive_cannot_materialize(self):
        manifest = self.manifest()
        manifest["startup"]["status_file"] = "runpod-execution/status.json"
        manifest["startup"]["heartbeat_file"] = "runpod-execution/monitor_events.ndjson"
        manifest["startup"]["log_file"] = "runpod-execution/logs/startup.log"
        manifest["artifact_egress"] = {
            "mode": "workspace_archive",
            "archive_path": "runpod-execution/artifacts/runpod-execution.tar.gz",
        }
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "summary",
                "path": "runpod-execution/artifacts/summary.json",
                "required": True,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_fetch = proxy_module.fetch_proxy_file

            def fake_fetch(pod_id, port, remote_path, output_path, timeout_seconds=30):
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                if remote_path == "runpod-execution/status.json":
                    output.write_text('{"status":"succeeded","phase":"complete","exit_code":0}\n')
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/monitor_events.ndjson":
                    output.write_text('{"ts":"2026-05-27T00:00:00Z","status":"alive"}\n')
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/logs/startup.log":
                    output.write_text("done\n")
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/artifact_hashes.jsonl":
                    output.write_text("")
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/artifacts/summary.json":
                    output.write_text('{"ok":true}\n')
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                if remote_path == "runpod-execution/artifacts/runpod-execution.tar.gz":
                    output.write_text("not a tar archive")
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                return {"ok": False, "url": "test", "output_path": str(output), "error": "not found"}

            proxy_module.fetch_proxy_file = fake_fetch
            try:
                result = proxy_module.verify_proxy_packet(
                    manifest,
                    "pod123",
                    port=8001,
                    out_dir=root / "out",
                    timeout_seconds=1,
                    interval_seconds=0,
                )
            finally:
                proxy_module.fetch_proxy_file = original_fetch

            self.assertFalse(result["ok"], result)
            self.assertFalse(result["archive_materialization"]["ok"], result)
            self.assertEqual(result["closeout"]["status"], "succeeded")

    def test_artifact_verification_fails_when_archive_materialization_fails(self):
        verification = {
            "ok": True,
            "status": {"status": "succeeded"},
            "archive_materialization": {"ok": False, "attempted": True, "error": "bad archive"},
            "closeout": {
                "status": "succeeded",
                "missing_required_artifacts": [],
                "artifacts": [
                    {
                        "artifact_id": "summary",
                        "required": True,
                        "present": True,
                        "sha256": "0" * 64,
                    }
                ],
            },
        }

        self.assertFalse(remote_run_module.artifact_verification_succeeded(verification))

    def test_proxy_packet_verifies_presigned_s3_upload_egress_from_trusted_uri(self):
        manifest = self.manifest()
        manifest["startup"]["status_file"] = "runpod-execution/status.json"
        manifest["startup"]["heartbeat_file"] = "runpod-execution/monitor_events.ndjson"
        manifest["startup"]["log_file"] = "runpod-execution/logs/startup.log"
        manifest["artifact_egress"] = {
            "mode": "aws_s3_presigned_upload",
            "archive_path": "runpod-execution/artifacts/runpod-execution.tar.gz",
            "archive_upload_url_ref": "env:RUNPOD_PRESIGNED_ARCHIVE_PUT_URL",
            "hash_upload_url_ref": "env:RUNPOD_PRESIGNED_HASH_PUT_URL",
            "archive_verify_uri_ref": "env:RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI",
            "hash_verify_uri_ref": "env:RUNPOD_PRESIGNED_HASH_VERIFY_URI",
            "requires_presigned_upload": True,
        }
        manifest["expected_artifacts"] = [
            {
                "artifact_id": "summary",
                "path": "runpod-execution/artifacts/summary.json",
                "required": True,
            },
            {
                "artifact_id": "workspace_archive",
                "path": "runpod-execution/artifacts/runpod-execution.tar.gz",
                "required": True,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "staged"
            staged_paths = {
                "runpod-execution/status.json": '{"status":"succeeded","phase":"complete","exit_code":0}\n',
                "runpod-execution/monitor_events.ndjson": '{"ts":"2026-05-27T00:00:00Z","status":"alive"}\n',
                "runpod-execution/logs/startup.log": "done\n",
                "runpod-execution/artifact_hashes.jsonl": '{"path":"runpod-execution/artifacts/summary.json","sha256":"abc"}\n',
                "runpod-execution/egress_status.json": '{"mode":"aws_s3_presigned_upload","status":"uploaded"}\n',
                "runpod-execution/artifacts/summary.json": '{"ok":true}\n',
            }
            for rel_path, content in staged_paths.items():
                target = staged / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            archive_path = root / "runpod-execution.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for rel_path in staged_paths:
                    archive.add(staged / rel_path, arcname=rel_path)

            uploaded_hash = root / "artifact_hashes.jsonl"
            uploaded_hash.write_text(staged_paths["runpod-execution/artifact_hashes.jsonl"])
            original_fetch = proxy_module.fetch_proxy_file

            def fake_fetch(pod_id, port, remote_path, output_path, timeout_seconds=30):
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                if remote_path == "runpod-execution/artifacts/runpod-execution.tar.gz":
                    output.write_bytes(archive_path.read_bytes())
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                content = staged_paths.get(remote_path)
                if content is not None:
                    output.write_text(content)
                    return {"ok": True, "url": "test", "output_path": str(output), "bytes": output.stat().st_size}
                return {"ok": False, "url": "test", "output_path": str(output), "error": "not found"}

            previous_archive_uri = os.environ.get("RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI")
            previous_hash_uri = os.environ.get("RUNPOD_PRESIGNED_HASH_VERIFY_URI")
            os.environ["RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI"] = f"file://{archive_path}"
            os.environ["RUNPOD_PRESIGNED_HASH_VERIFY_URI"] = f"file://{uploaded_hash}"
            proxy_module.fetch_proxy_file = fake_fetch
            try:
                result = proxy_module.verify_proxy_packet(
                    manifest,
                    "pod123",
                    port=8001,
                    out_dir=root / "out",
                    timeout_seconds=1,
                    interval_seconds=0,
                )
            finally:
                proxy_module.fetch_proxy_file = original_fetch
                if previous_archive_uri is None:
                    os.environ.pop("RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI", None)
                else:
                    os.environ["RUNPOD_PRESIGNED_ARCHIVE_VERIFY_URI"] = previous_archive_uri
                if previous_hash_uri is None:
                    os.environ.pop("RUNPOD_PRESIGNED_HASH_VERIFY_URI", None)
                else:
                    os.environ["RUNPOD_PRESIGNED_HASH_VERIFY_URI"] = previous_hash_uri

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["presigned_egress_verification"]["ok"], result)
            self.assertTrue(result["closeout"]["egress_ok"], result)
            self.assertEqual(result["closeout"]["egress_status"]["status"], "verified")

    def test_artifact_verification_fails_when_presigned_egress_fails(self):
        verification = {
            "ok": True,
            "status": {"status": "succeeded"},
            "presigned_egress_verification": {"ok": False, "attempted": True, "error": "missing object"},
            "closeout": {
                "status": "succeeded",
                "missing_required_artifacts": [],
                "artifacts": [
                    {
                        "artifact_id": "summary",
                        "required": True,
                        "present": True,
                        "sha256": "0" * 64,
                    }
                ],
            },
        }

        self.assertFalse(remote_run_module.artifact_verification_succeeded(verification))

    def test_tcp_endpoint_from_pod_handles_port_mapping_shapes(self):
        pod = {
            "id": "pod123",
            "publicIp": "203.0.113.10",
            "portMappings": [
                {"privatePort": 22, "publicPort": 31022, "type": "tcp"},
                {"privatePort": 8000, "publicPort": 31800, "type": "tcp"},
            ],
        }
        self.assertEqual(tcp_endpoint_from_pod(pod, 8000), ("203.0.113.10", 31800))
        self.assertEqual(
            tcp_url("203.0.113.10", 31800, "runpod-execution/status.json"),
            "http://203.0.113.10:31800/runpod-execution/status.json",
        )

    def test_tcp_endpoint_from_runtime_report_handles_graphql_ports(self):
        runtime = {
            "metrics": {
                "ports": [
                    {"ip": "203.0.113.20", "privatePort": 8000, "publicPort": 31800, "type": "tcp"},
                    {"ip": "100.64.0.2", "privatePort": 19123, "publicPort": 32123, "type": "http"},
                ]
            }
        }
        self.assertEqual(tcp_endpoint_from_runtime_report(runtime, 8000), ("203.0.113.20", 31800))

    def test_verify_tcp_with_client_falls_back_to_runtime_ports(self):
        manifest = self.manifest()
        seen: dict[str, object] = {}

        class Client:
            def get_pod(self, pod_id):
                return {"id": pod_id, "publicIp": "", "portMappings": None}

        def fake_runtime(pod_id):
            seen["runtime_pod_id"] = pod_id
            return {
                "ok": True,
                "metrics": {
                    "ports": [
                        {"ip": "203.0.113.20", "privatePort": 8002, "publicPort": 31802, "type": "tcp"},
                    ]
                },
            }

        def fake_verify(manifest_arg, pod, *, internal_port, out_dir, timeout_seconds, interval_seconds, progress_callback=None):
            seen["verify_pod"] = pod
            seen["internal_port"] = internal_port
            return {"ok": True, "pod_id": pod["id"], "status": {"status": "succeeded"}}

        original_runtime = remote_run_module.build_runtime_metrics_report
        original_verify = remote_run_module.verify_tcp_packet
        remote_run_module.build_runtime_metrics_report = fake_runtime
        remote_run_module.verify_tcp_packet = fake_verify
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = remote_run_module.verify_tcp_with_client(
                    manifest,
                    "pod-123",
                    Client(),
                    8002,
                    Path(tmp),
                    timeout_seconds=5,
                    interval_seconds=0,
                )
        finally:
            remote_run_module.build_runtime_metrics_report = original_runtime
            remote_run_module.verify_tcp_packet = original_verify

        self.assertTrue(result["ok"], result)
        self.assertEqual(seen["runtime_pod_id"], "pod-123")
        self.assertEqual(seen["internal_port"], 8002)
        self.assertEqual(tcp_endpoint_from_pod(seen["verify_pod"], 8002), ("203.0.113.20", 31802))

    def test_default_packet_verifier_uses_declared_artifact_port(self):
        manifest = self.manifest()
        manifest["startup"]["inspection"] = {"http_artifact_server_port": 8001}
        seen: dict[str, object] = {}

        def fake_verify(manifest_arg, pod_id, *, port, out_dir, timeout_seconds, interval_seconds, progress_callback=None):
            seen["port"] = port
            return {"ok": True, "pod_id": pod_id, "status": {"status": "succeeded"}, "closeout": {"status": "succeeded"}}

        original_verify = remote_run_module.verify_proxy_packet
        remote_run_module.verify_proxy_packet = fake_verify
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = remote_run_module.default_packet_verifier(
                    "proxy",
                    manifest,
                    "pod-123",
                    object(),
                    8000,
                    Path(tmp),
                    5,
                    0,
                )
        finally:
            remote_run_module.verify_proxy_packet = original_verify

        self.assertTrue(result["ok"], result)
        self.assertEqual(seen["port"], 8001)

    def test_create_pod_flow_blocks_when_remote_not_allowed(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=False)
            self.assertEqual(record["status"], "blocked")
            self.assertTrue((Path(tmp) / "runpod_resource_record.json").is_file())

    def test_create_pod_flow_blocks_over_spend_ceiling(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        manifest["runpod"]["image_capabilities"] = ["git"]
        manifest["repo"]["source_proof"] = {"status": "reachable", "url": manifest["repo"]["url_or_path"], "ref": manifest["repo"]["commit_or_snapshot"]}
        manifest["runpod"]["ports"] = ["8000/http", "8000/tcp"]
        manifest["access"]["http_proxy_required"] = True
        manifest["access"]["tcp_ports_required"] = True
        manifest["startup"]["inspection"] = {"hold_after_success_seconds": 180, "http_artifact_server_port": 8000}
        manifest["budget"]["max_estimated_cost_usd"] = 10
        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=False, max_spend_usd=5)
            self.assertEqual(record["status"], "blocked_spend_ceiling")

    def test_create_pod_flow_records_duplicate_check_api_failure(self):
        class FailingListClient:
            def list_pods(self, name=None):
                raise RunpodRestError("dns unavailable")

        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        manifest["runpod"]["image_capabilities"] = ["git"]
        manifest["repo"]["source_proof"] = {"status": "reachable", "url": manifest["repo"]["url_or_path"], "ref": manifest["repo"]["commit_or_snapshot"]}
        manifest["runpod"]["ports"] = ["8000/http", "8000/tcp"]
        manifest["access"]["http_proxy_required"] = True
        manifest["access"]["tcp_ports_required"] = True
        manifest["startup"]["inspection"] = {"hold_after_success_seconds": 180, "http_artifact_server_port": 8000}
        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=True, client=FailingListClient())
            self.assertEqual(record["status"], "failed_duplicate_check")
            self.assertIn("dns unavailable", record["error"])
            saved = json.loads((Path(tmp) / "runpod_resource_record.json").read_text())
            self.assertEqual(saved["status"], "failed_duplicate_check")

    def test_create_pod_flow_records_create_api_failure(self):
        class FailingCreateClient:
            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                raise RunpodRestError("post failed")

        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["repo"]["url_or_path"] = "https://github.com/example/repo.git"
        manifest["repo"]["commit_or_snapshot"] = "0123456789abcdef0123456789abcdef01234567"
        manifest["runpod"]["image_capabilities"] = ["git"]
        manifest["repo"]["source_proof"] = {"status": "reachable", "url": manifest["repo"]["url_or_path"], "ref": manifest["repo"]["commit_or_snapshot"]}
        manifest["runpod"]["ports"] = ["8000/http", "8000/tcp"]
        manifest["access"]["http_proxy_required"] = True
        manifest["access"]["tcp_ports_required"] = True
        manifest["startup"]["inspection"] = {"hold_after_success_seconds": 180, "http_artifact_server_port": 8000}
        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=True, client=FailingCreateClient())
            self.assertEqual(record["status"], "failed_create_request")
            self.assertIn("creation state is unknown", record["blockers"][0])
            saved = json.loads((Path(tmp) / "runpod_resource_record.json").read_text())
            self.assertEqual(saved["duplicate_check"]["active_matches"], [])

    def test_create_pod_flow_blocks_gpu_catalog_mismatch_before_rest_create(self):
        class NoCreateClient:
            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                raise AssertionError("create_pod should not be called for catalog mismatch")

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-05-16T00:00:00Z",
        }
        manifest["runpod"]["cloudType"] = "SECURE"
        manifest["runpod"]["gpuCount"] = 1
        manifest["runpod"]["gpuTypeIds"] = ["NVIDIA L40"]
        manifest["runpod"]["dataCenterIds"] = ["US-KS-2"]

        original = runpod_rest_module.build_gpu_catalog_report_from_manifest
        runpod_rest_module.build_gpu_catalog_report_from_manifest = lambda _manifest: {
            "summary": {"requested_gpu_type_count": 1, "offered_requested_combo_count": 0},
            "constraints_satisfied": False,
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                record = create_pod_flow(manifest, out_dir=tmp, execute=True, client=NoCreateClient())
                self.assertEqual(record["status"], "blocked_gpu_catalog")
                self.assertIn("catalog mismatch", record["blockers"][0])
        finally:
            runpod_rest_module.build_gpu_catalog_report_from_manifest = original

    def test_create_pod_flow_rejects_created_response_without_pod_id(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }

        class MissingIdClient:
            def __init__(self):
                self.calls = 0

            def list_pods(self, name=None):
                self.calls += 1
                if self.calls == 1:
                    return []
                return [{"id": "pod-lost", "name": manifest["runpod"]["name"], "desiredStatus": "RUNNING"}]

            def create_pod(self, body):
                return {"name": body["name"], "desiredStatus": "RUNNING"}

        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=True, client=MissingIdClient())
            self.assertEqual(record["status"], "created_missing_pod_id")
            self.assertIn("no pod id", record["blockers"][0])
            self.assertEqual(record["recovery_candidates"][0]["id"], "pod-lost")

    def test_duplicate_detection_matches_active_prefix(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        pods = [
            {"id": "active", "name": "symphony-cheap-pod-dryrun-1", "desiredStatus": "RUNNING"},
            {"id": "gone", "name": "symphony-cheap-pod-dryrun-2", "desiredStatus": "TERMINATED"},
        ]
        matches = active_duplicate_pods(pods, manifest)
        self.assertEqual([pod["id"] for pod in matches], ["active"])

    def test_cleanup_pod_flow_dry_run_writes_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = cleanup_pod_flow("pod-123", out_dir=tmp, action="delete", execute=False)
            self.assertEqual(record["status"], "dry_run_request")
            self.assertTrue((Path(tmp) / "runpod_cleanup_record.json").is_file())

    def test_cleanup_pod_flow_treats_delete_404_as_absent(self):
        class MissingPodClient:
            def delete_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        with tempfile.TemporaryDirectory() as tmp:
            record = cleanup_pod_flow("pod-123", out_dir=tmp, action="delete", execute=True, client=MissingPodClient())
            self.assertEqual(record["status"], "already_absent")

    def test_cli_cleanup_pod_submitted_prints_unverified_next_action(self):
        def fake_cleanup_pod_flow(*args, **kwargs):
            return {"status": "submitted"}

        original = cli_module.cleanup_pod_flow
        cli_module.cleanup_pod_flow = fake_cleanup_pod_flow
        try:
            with tempfile.TemporaryDirectory() as tmp:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    rc = cli_main([
                        "cleanup-pod",
                        "pod-123",
                        "--action",
                        "delete",
                        "--out-dir",
                        tmp,
                        "--execute",
                        "--yes-cleanup-runpod",
                    ])
        finally:
            cli_module.cleanup_pod_flow = original

        self.assertEqual(rc, 2)
        text = stdout.getvalue()
        self.assertIn("status: submitted", text)
        self.assertIn("cleanup_verified: false", text)
        self.assertIn("next_action: rerun cleanup-pod with --wait", text)

    def test_verify_cleanup_delete_requires_absence_not_terminated_status(self):
        class StillVisibleClient:
            def get_pod(self, pod_id):
                return {"id": pod_id, "name": "test", "desiredStatus": "TERMINATED"}

        record = verify_cleanup(StillVisibleClient(), "pod-123", "delete", timeout_seconds=0.01, interval_seconds=0)
        self.assertFalse(record["ok"])
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(record["last_pod"]["desiredStatus"], "TERMINATED")
        self.assertIn("absence is not verified", record["error"])

    def test_run_remote_flow_verifies_and_cleans_up_created_pod(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []
                self.record_path = None
                self.status_before_cleanup = ""

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING", "costPerHr": 0.06}

            def delete_pod(self, pod_id):
                if self.record_path:
                    self.status_before_cleanup = json.loads(Path(self.record_path).read_text())["status"]
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {
                "ok": True,
                "mode": "tcp",
                "pod_id": pod_id,
                "status": {"status": "succeeded"},
                "closeout": {
                    "status": "succeeded",
                    "missing_required_artifacts": [],
                    "artifacts": [
                        {
                            "artifact_id": "result",
                            "required": True,
                            "present": True,
                            "sha256": "a" * 64,
                        }
                    ],
                },
            }

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            client.record_path = base / "run" / "remote_run_record.json"
            record = run_remote_flow(
                manifest,
                out_dir=base / "run",
                execute=True,
                max_spend_usd=1,
                lock_dir=base / "locks",
                client=client,
                packet_verifier=verifier,
            )
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["cleanup"]["status"], "verified")
            self.assertEqual(client.status_before_cleanup, "artifacts_verified_cleanup_pending")
            self.assertTrue(record["launch_lock"]["released"])
            self.assertFalse(list((base / "locks").glob("*.lock.json")))
            self.assertEqual(client.deleted, ["pod-123"])
            saved = json.loads((base / "run" / "remote_run_record.json").read_text())
            self.assertEqual(saved["verification"]["closeout"]["status"], "succeeded")
            self.assertTrue(saved["launch_lock"]["released"])

    def test_run_remote_flow_success_with_no_wait_cleanup_is_not_final_success(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING", "costPerHr": 0.06}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {
                "ok": True,
                "mode": "tcp",
                "pod_id": pod_id,
                "status": {"status": "succeeded"},
                "closeout": {
                    "status": "succeeded",
                    "missing_required_artifacts": [],
                    "artifacts": [{"artifact_id": "result", "required": True, "present": True, "sha256": "a" * 64}],
                },
            }

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            record = run_remote_flow(
                manifest,
                out_dir=tmp,
                execute=True,
                max_spend_usd=1,
                client=client,
                packet_verifier=verifier,
                cleanup_wait=False,
            )
            self.assertEqual(record["status"], "cleanup_unverified")
            self.assertEqual(record["cleanup"]["status"], "submitted")
            self.assertEqual(client.deleted, ["pod-123"])

    def test_run_remote_flow_blocks_when_launch_lock_exists(self):
        class NoCallClient:
            def list_pods(self, name=None):
                raise AssertionError("list_pods should not run when launch lock is held")

            def create_pod(self, body):
                raise AssertionError("create_pod should not run when launch lock is held")

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lock = acquire_launch_lock(manifest, out_dir=base / "owner", lock_dir=base / "locks")
            try:
                record = run_remote_flow(
                    manifest,
                    out_dir=base / "blocked",
                    execute=True,
                    lock_dir=base / "locks",
                    client=NoCallClient(),
                )
                self.assertEqual(record["status"], "blocked_launch_lock")
                self.assertEqual(record["launch_lock"]["status"], "held")
                self.assertFalse(record["launch_lock"]["acquired"])
            finally:
                release_launch_lock(lock)

    def test_run_remote_flow_cleans_up_after_verification_failure(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING"}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {"ok": False, "mode": "tcp", "pod_id": pod_id, "error": "artifact missing"}

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            record = run_remote_flow(manifest, out_dir=tmp, execute=True, client=client, packet_verifier=verifier)
            self.assertEqual(record["status"], "verification_failed")
            self.assertEqual(record["cleanup"]["status"], "verified")
            self.assertEqual(client.deleted, ["pod-123"])

    def test_run_remote_flow_rejects_unproven_verifier_success(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING"}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {"ok": True, "mode": "tcp", "pod_id": pod_id}

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            record = run_remote_flow(manifest, out_dir=tmp, execute=True, client=client, packet_verifier=verifier)
            self.assertEqual(record["status"], "verification_failed")
            self.assertEqual(record["cleanup"]["status"], "verified")
            self.assertEqual(client.deleted, ["pod-123"])

    def test_run_remote_auto_uses_s3_volume_verifier_before_cleanup(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING"}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["runpod"]["networkVolumeId"] = "network-volume-id"
        manifest["artifact_egress"] = {
            "mode": "runpod_network_volume_s3",
            "archive_path": "/workspace/runpod-execution/artifacts/runpod-execution.tar.gz",
            "data_center_id": "US-KS-2",
            "credentials_ref": "env:RUNPOD_NETWORK_VOLUME_S3_PROFILE",
            "requires_network_volume": True,
        }
        client = FakeClient()
        cleanup_seen_by_verifier = []

        def fake_verify_network_volume_s3(
            manifest,
            *,
            out_dir,
            execute=False,
            timeout_seconds=180,
            interval_seconds=5,
            runner=None,
        ):
            cleanup_seen_by_verifier.append(list(client.deleted))
            return {
                "ok": True,
                "status": "succeeded",
                "closeout": {
                    "status": "succeeded",
                    "workload_status": {"status": "succeeded"},
                    "missing_required_artifacts": [],
                    "artifacts": [
                        {
                            "artifact_id": "result",
                            "required": True,
                            "present": True,
                            "sha256": "b" * 64,
                        }
                    ],
                },
            }

        original = remote_run_module.verify_network_volume_s3
        remote_run_module.verify_network_volume_s3 = fake_verify_network_volume_s3
        try:
            with tempfile.TemporaryDirectory() as tmp:
                record = run_remote_flow(
                    manifest,
                    out_dir=tmp,
                    execute=True,
                    max_spend_usd=1,
                    client=client,
                    verification_mode="auto",
                )
        finally:
            remote_run_module.verify_network_volume_s3 = original

        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["verification"]["mode"], "runpod_network_volume_s3")
        self.assertEqual(record["cleanup"]["status"], "verified")
        self.assertEqual(client.deleted, ["pod-123"])
        self.assertEqual(cleanup_seen_by_verifier, [[]])

    def test_run_remote_flow_cleans_up_after_verifier_exception(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING"}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            raise RuntimeError("proxy crashed")

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            record = run_remote_flow(manifest, out_dir=tmp, execute=True, client=client, packet_verifier=verifier)
            self.assertEqual(record["status"], "verification_error")
            self.assertEqual(record["cleanup"]["status"], "verified")
            self.assertEqual(record["verification"]["error_type"], "RuntimeError")
            self.assertEqual(client.deleted, ["pod-123"])

    def test_provider_handoff_validates_remote_ready_manifest(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "launch_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handoff = write_provider_handoff(
                manifest,
                manifest_path=manifest_path,
                out_path=base / "provider_handoff.json",
                reason="worker_network_unreachable",
                worker_id="worker-1",
            )
            self.assertEqual(handoff["status"], "ready_for_orchestrator")
            validation = validate_provider_handoff(handoff, handoff_path=base / "provider_handoff.json")
            self.assertTrue(validation["ok"], validation)
            self.assertEqual(validation["run_id"], "public-inline-smoke")

    def test_run_handoff_flow_uses_handoff_defaults_and_cleans_up(self):
        class FakeClient:
            def __init__(self):
                self.deleted = []

            def list_pods(self, name=None):
                return []

            def create_pod(self, body):
                return {"id": "pod-123", "name": body["name"], "desiredStatus": "RUNNING", "costPerHr": 0.06}

            def delete_pod(self, pod_id):
                self.deleted.append(pod_id)
                return {"pod_id": pod_id, "action": "delete"}

            def get_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {
                "ok": True,
                "mode": mode,
                "pod_id": pod_id,
                "status": {"status": "succeeded"},
                "closeout": {
                    "status": "succeeded",
                    "missing_required_artifacts": [],
                    "artifacts": [
                        {
                            "artifact_id": "result",
                            "required": True,
                            "present": True,
                            "sha256": "a" * 64,
                        }
                    ],
                },
            }

        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "launch_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handoff_path = base / "provider_handoff.json"
            write_provider_handoff(
                manifest,
                manifest_path=manifest_path,
                out_path=handoff_path,
                reason="worker_network_unreachable",
                verification_mode="tcp",
            )
            record = run_handoff_flow(
                handoff_path,
                out_dir=base / "handoff-run",
                execute=True,
                max_spend_usd=1,
                client=client,
                packet_verifier=verifier,
            )
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["remote_execution_by"], "orchestrator")
            self.assertEqual(record["remote_run"]["verification"]["mode"], "tcp")
            self.assertEqual(client.deleted, ["pod-123"])

    def test_provider_capabilities_and_profiles(self):
        capabilities = provider_capabilities("runpod")
        self.assertTrue(capabilities["automated_launch"])
        self.assertIn("billing", capabilities["resources"])
        self.assertIn("flash", capabilities["resources"])
        self.assertIn("runtime_metrics", capabilities["resources"])
        self.assertIn("gpu_catalog", capabilities["resources"])
        self.assertIn("serverless_endpoints", capabilities["resources"])
        self.assertIn("aws_integrations", capabilities["resources"])
        self.assertIn("runtime-metrics", capabilities["resources"]["pods"]["bridge_commands"])
        self.assertIn("gpu-catalog", capabilities["resources"]["pods"]["bridge_commands"])
        self.assertIn("aws_s3_presigned_upload", capabilities["artifact_egress"]["production"])
        self.assertIn("runpod_flash_v1", {item["name"] for item in capabilities["next_adapters"]})
        self.assertIn("billing-endpoints", capabilities["resources"]["billing"]["bridge_commands"])
        self.assertIn("billing.cost_center", capabilities["resources"]["billing"]["manifest_fields"])
        self.assertIn("3.13", capabilities["resources"]["flash"]["python_versions"])
        self.assertIn("interruptible_pods", capabilities["resources"])
        huge = get_profile("huge-sharded-volume")
        self.assertEqual(huge["workload_scale"], "huge")
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        self.assertEqual(recommend_profile(manifest)["name"], "huge-sharded-volume")

    def test_egress_and_preflight_surface_durable_requirements(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        egress = build_egress_plan(manifest)
        self.assertEqual(egress["mode"], "object_store_upload")
        self.assertTrue(egress["durable"])
        preflight = analyze_preflight(manifest)
        self.assertIn("recommended_profile", preflight)
        self.assertEqual(preflight["recommended_profile"]["name"], "huge-sharded-volume")
        self.assertTrue(preflight["productivity"]["has_live_productivity_channel"], preflight["productivity"])

    def test_preflight_blocks_nontrivial_remote_launch_without_live_productivity(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["remote_launch_allowed"] = True
        manifest["launch_authorization"] = {
            "source": "test",
            "approved_by": "test",
            "approved_at": "2026-04-30T00:00:00Z",
        }
        manifest["workload"]["scale"] = "large"
        manifest["budget"]["max_runtime_minutes"] = 90
        manifest["budget"]["max_estimated_cost_usd"] = 10
        manifest["startup"].pop("inspection", None)
        manifest["startup"].pop("progress", None)
        manifest["runpod"]["ports"] = []
        manifest["access"]["http_proxy_required"] = False
        manifest["access"]["tcp_ports_required"] = False
        preflight = analyze_preflight(manifest)
        self.assertFalse(preflight["ok"], preflight)
        self.assertFalse(preflight["productivity"]["has_live_productivity_channel"])
        self.assertTrue(any(issue["path"] == "productivity" for issue in preflight["errors"]))

    def test_runpod_network_volume_s3_egress_mode(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        manifest["artifact_egress"] = {
            "mode": "runpod_network_volume_s3",
            "archive_path": "/workspace/runpod-execution/artifacts/runpod-execution.tar.gz",
            "data_center_id": "US-KS-2",
            "credentials_ref": "env:RUNPOD_NETWORK_VOLUME_S3_PROFILE",
            "requires_network_volume": True,
        }
        validation = validate_manifest(manifest)
        self.assertTrue(validation.ok, validation.as_dict())
        egress = build_egress_plan(manifest)
        self.assertTrue(egress["durable"])
        self.assertIn("AWS_ACCESS_KEY_ID", egress["required_env"])
        self.assertTrue(any("s3api-us-ks-2.runpod.io" in command for command in egress["commands"]))
        self.assertTrue(any("runpod-execution/artifacts/runpod-execution.tar.gz" in command for command in egress["commands"]))

    def test_network_volume_s3_verifier_downloads_extracts_and_closes_out(self):
        manifest = load_manifest(ROOT / "examples" / "runpod-network-volume-s3" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = base / "payload"
            execution = payload / "runpod-execution"
            artifacts = execution / "artifacts"
            artifacts.mkdir(parents=True)
            (execution / "logs").mkdir(parents=True)
            (execution / "status.json").write_text(json.dumps({"status": "succeeded", "exit_code": 0}))
            (execution / "monitor_events.ndjson").write_text('{"phase":"startup","status":"succeeded"}\n')
            (execution / "logs" / "startup.log").write_text("done\n")
            (execution / "artifact_hashes.jsonl").write_text('{"path":"runpod-execution/artifacts/network-volume-s3-result.json"}\n')
            (artifacts / "network-volume-s3-result.json").write_text('{"ok": true}\n')
            archive = base / "download-source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(execution, arcname="runpod-execution")

            head_attempts = {"count": 0}

            def runner(command):
                if command[:2] == ["aws", "s3api"]:
                    head_attempts["count"] += 1
                    if head_attempts["count"] == 1:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    return subprocess.CompletedProcess(command, 0, "{}", "")
                if command[:3] == ["aws", "s3", "cp"]:
                    Path(command[-1]).write_bytes(archive.read_bytes())
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 1, "", "unexpected command")

            result = verify_network_volume_s3(
                manifest,
                out_dir=base / "verify",
                execute=True,
                timeout_seconds=5,
                interval_seconds=0,
                runner=runner,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(result["head_attempts"]), 2)
            self.assertEqual(result["closeout"]["status"], "succeeded")
            self.assertTrue(result["archive_sha256"])
            egress_status = json.loads((base / "verify" / "extracted" / "runpod-execution" / "egress_status.json").read_text())
            self.assertEqual(egress_status["status"], "verified")
            self.assertEqual(egress_status["mode"], "runpod_network_volume_s3")
            self.assertTrue((base / "verify" / "extracted" / "runpod-execution" / "artifacts" / "runpod-execution.tar.gz").is_file())

            plan = build_network_volume_s3_verify_plan(manifest, base / "plan")
            self.assertIn("AWS_ACCESS_KEY_ID", plan["required_env"])
            self.assertTrue(any("aws s3api head-object" in command for command in plan["commands"]))

    def test_network_volume_s3_plan_expands_endpoint_env_for_execution(self):
        manifest = load_manifest(ROOT / "examples" / "runpod-network-volume-s3" / "launch_manifest.json")
        manifest["artifact_egress"]["s3_endpoint_url_ref"] = "env:RUNPOD_TEST_S3_ENDPOINT"
        old_value = os.environ.get("RUNPOD_TEST_S3_ENDPOINT")
        os.environ["RUNPOD_TEST_S3_ENDPOINT"] = "https://s3api-test.runpod.io/"
        try:
            plan = build_network_volume_s3_verify_plan(manifest, Path(tempfile.gettempdir()) / "runpod-s3-plan")
        finally:
            if old_value is None:
                os.environ.pop("RUNPOD_TEST_S3_ENDPOINT", None)
            else:
                os.environ["RUNPOD_TEST_S3_ENDPOINT"] = old_value
        self.assertIn("RUNPOD_TEST_S3_ENDPOINT", plan["required_env"])
        self.assertEqual(plan["endpoint_url"], "https://s3api-test.runpod.io/")
        self.assertIn("https://s3api-test.runpod.io/", plan["head_command"])

    def test_safe_extract_rejects_symlink_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("runpod-execution/escape")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                tar.addfile(info)
            with tarfile.open(archive, "r:gz") as tar:
                with self.assertRaises(tarfile.TarError):
                    safe_extract(tar, base / "out")

    def test_aws_orchestrator_plan_surfaces_companion_lanes(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        manifest["artifact_egress"].update(
            {
                "destination_uri": "s3://runpod-artifacts/runs/aws-plan",
                "destination_uri_ref": "",
                "credentials_ref": "aws-sts:arn:aws:iam::123456789012:role/runpod-artifact-upload",
            }
        )
        manifest["runpod"]["imageName"] = "123456789012.dkr.ecr.us-west-2.amazonaws.com/runpod-worker:sha"
        manifest["runpod"]["env"]["RUNTIME_SECRET_REF"] = "aws-sm:runpod/demo/secret"
        manifest["aws"] = {
            "region_ref": "env:AWS_REGION",
            "sqs": {
                "queue_url_ref": "env:RUNPOD_BRIDGE_SQS_QUEUE_URL",
                "fifo": True,
                "visibility_timeout_seconds": 600,
            },
            "dynamodb": {
                "lock_table_ref": "env:RUNPOD_BRIDGE_LOCK_TABLE",
                "ttl_seconds": 900,
            },
            "eventbridge_cleanup": {
                "enabled": True,
                "role_arn_ref": "env:RUNPOD_BRIDGE_SCHEDULER_ROLE_ARN",
                "target_arn_ref": "env:RUNPOD_BRIDGE_CLEANUP_TARGET_ARN",
                "dead_letter_queue_arn_ref": "env:RUNPOD_BRIDGE_CLEANUP_DLQ_ARN",
            },
            "ecr": {
                "runpod_registry_auth_name": "runpod-ecr-test",
            },
        }
        validation = validate_manifest(manifest)
        self.assertTrue(validation.ok, validation.as_dict())
        plan = build_aws_orchestrator_plan(
            manifest,
            handoff_path="runpod-execution/provider_handoff.json",
            now_utc=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(plan["ok"], plan)
        self.assertIn("AWS_REGION", plan["required_env"])
        self.assertIn("RUNPOD_BRIDGE_SQS_QUEUE_URL", plan["required_env"])
        self.assertIn("RUNPOD_BRIDGE_LOCK_TABLE", plan["required_env"])
        self.assertIn("RUNPOD_BRIDGE_CLEANUP_TARGET_ARN", plan["required_env"])
        self.assertEqual(plan["features"]["sts_scoped_object_store_upload"]["status"], "configured")
        self.assertIn("aws-sts-s3-upload-policy.json", plan["features"]["sts_scoped_object_store_upload"]["helper_files"])
        self.assertTrue(any("aws sts assume-role" in command for command in plan["features"]["sts_scoped_object_store_upload"]["commands"]))
        self.assertTrue(any("runpodctl registry create" in command for command in plan["features"]["ecr_registry_refresh"]["commands"]))
        self.assertTrue(any("aws secretsmanager get-secret-value" in command for command in plan["features"]["secrets_manager_refs"]["commands"]))
        self.assertTrue(any("aws sqs send-message" in command for command in plan["features"]["sqs_handoff_queue"]["commands"]))
        self.assertTrue(any("aws dynamodb put-item" in command for command in plan["features"]["dynamodb_launch_lock"]["commands"]))
        self.assertTrue(any("aws scheduler create-schedule" in command for command in plan["features"]["eventbridge_cleanup_backstop"]["commands"]))
        target = plan["features"]["eventbridge_cleanup_backstop"]["helper_files"]["aws-eventbridge-cleanup-target.template.json"]
        self.assertEqual(target["RoleArn"], "$RUNPOD_BRIDGE_SCHEDULER_ROLE_ARN")
        self.assertIn("aws", plan["docs_checked"])

    def test_aws_orchestrator_plan_command_writes_helpers(self):
        manifest = load_manifest(ROOT / "examples" / "cheap-pod" / "launch_manifest.json")
        manifest["aws"] = {
            "dynamodb": {"lock_table_ref": "env:RUNPOD_BRIDGE_LOCK_TABLE"},
            "eventbridge_cleanup": {
                "enabled": True,
                "role_arn_ref": "env:RUNPOD_BRIDGE_SCHEDULER_ROLE_ARN",
                "target_arn_ref": "env:RUNPOD_BRIDGE_CLEANUP_TARGET_ARN",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            helper_dir = Path(tmp) / "aws-helpers"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            rc = cli_main(["aws-orchestrator-plan", str(manifest_path), "--out-dir", str(helper_dir)])
            self.assertEqual(rc, 0)
            self.assertTrue((helper_dir / "aws-dynamodb-lock-put.template.json").is_file())
            self.assertTrue((helper_dir / "aws-eventbridge-cleanup-target.template.json").is_file())
            self.assertTrue((helper_dir / "aws-orchestrator-helper-index.json").is_file())

    def test_aws_examples_validate_and_surface_plans(self):
        aws_manifest = load_manifest(ROOT / "examples" / "aws-orchestrated" / "launch_manifest.json")
        self.assertTrue(validate_manifest(aws_manifest).ok)
        self.assertTrue(contract_self_check(aws_manifest)["ok"])
        aws_plan = build_aws_orchestrator_plan(
            aws_manifest,
            handoff_path="runpod-execution/provider_handoff.json",
            now_utc=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
        )
        for feature_name in (
            "sts_scoped_object_store_upload",
            "ecr_registry_refresh",
            "secrets_manager_refs",
            "sqs_handoff_queue",
            "dynamodb_launch_lock",
            "eventbridge_cleanup_backstop",
        ):
            self.assertEqual(aws_plan["features"][feature_name]["status"], "configured", feature_name)

        volume_manifest = load_manifest(ROOT / "examples" / "runpod-network-volume-s3" / "launch_manifest.json")
        self.assertTrue(validate_manifest(volume_manifest).ok)
        self.assertTrue(contract_self_check(volume_manifest)["ok"])
        egress = build_egress_plan(volume_manifest)
        self.assertTrue(egress["ok"], egress)
        self.assertIn("AWS_ACCESS_KEY_ID", egress["required_env"])
        volume_plan = build_aws_orchestrator_plan(volume_manifest)
        self.assertEqual(volume_plan["features"]["runpod_network_volume_s3"]["status"], "configured")

    def test_cost_report_uses_billing_when_requested(self):
        class BillingClient:
            def billing_pods(self, **query):
                self.query = query
                return [{"podId": query["podId"], "amount": 0.042, "timeBilledMs": 120000}]

        record = {
            "action": "run_remote",
            "ts": "2026-04-30T22:00:00Z",
            "manifest_run_id": "cost-smoke",
            "status": "succeeded",
            "create": {
                "pod_id": "pod-123",
                "pod": {
                    "id": "pod-123",
                    "name": "cost-smoke",
                    "costPerHr": 0.06,
                    "lastStartedAt": "2026-04-30T21:58:00Z",
                },
            },
            "cleanup": {"status": "submitted", "ts": "2026-04-30T22:00:00Z"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote_run_record.json"
            path.write_text(json.dumps(record))
            report = cost_report_from_record(path, fetch_billing=True, client=BillingClient())
            self.assertEqual(report["billing"]["amount_usd"], 0.042)
            self.assertGreater(report["estimate"]["amount_usd"], 0)

    def test_remote_outcome_includes_cleanup_and_artifact_hashes(self):
        record = {
            "action": "run_remote",
            "ts": "2026-04-30T22:00:00Z",
            "manifest_run_id": "outcome-smoke",
            "status": "succeeded",
            "create": {"pod_id": "pod-123", "pod": {"id": "pod-123", "desiredStatus": "TERMINATED", "costPerHr": 0.06}},
            "verification": {
                "ok": True,
                "status": {"status": "succeeded"},
                "closeout": {
                    "status": "succeeded",
                    "egress_ok": True,
                    "egress_status": {"mode": "workspace_archive", "status": "uploaded"},
                    "missing_required_artifacts": [],
                    "missing_required_evidence": [],
                    "forbidden_artifact_markers_enforced": True,
                    "forbidden_artifact_markers": [],
                    "artifacts": [{"artifact_id": "result", "path": "runpod-execution/artifacts/result.txt", "sha256": "abc"}],
                },
            },
            "cleanup": {"status": "verified"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            create_dir = base / "create"
            create_dir.mkdir()
            create_record_path = create_dir / "runpod_resource_record.json"
            create_record_path.write_text(json.dumps({
                "request": {"imageName": "python:3.12-slim", "templateId": "tpl-123"},
                "preview": {"plan": {"compute": {"profile": "small-cpu"}}},
                "response": {"id": "pod-123", "dataCenterId": "US-TEST"},
            }))
            record["create"]["record_path"] = str(create_record_path)
            path = base / "remote_run_record.json"
            path.write_text(json.dumps(record))
            (base / "remote_progress_latest.json").write_text(json.dumps({
                "classification": {
                    "state": "workload_progressing",
                    "workload_progressing": True,
                    "monitor_alive": True,
                    "outage_suspected": False,
                    "next_action": "continue_monitoring_with_previous_progress_report",
                }
            }))
            payload = write_remote_outcome(path, base / "symphony_outcome.md")
            text = Path(payload["outcome_path"]).read_text()
            self.assertIn("pod_id: pod-123", text)
            self.assertIn("pack_issue_id: outcome-smoke", text)
            self.assertIn("compute_profile: small-cpu", text)
            self.assertIn("image: python:3.12-slim", text)
            self.assertIn("cleanup_verified: true", text)
            self.assertIn("result: abc", text)
            self.assertIn("verified: true", text)
            self.assertIn("missing_required_artifacts:\n  - none", text)
            self.assertIn("missing_required_evidence:\n  - none", text)
            self.assertIn("artifact_marker_scan:", text)
            self.assertIn("findings: 0", text)
            self.assertIn("progress_report_classification_state: workload_progressing", text)
            self.assertIn("progress_report_workload_progressing: true", text)

    def test_supervisor_recommends_checkpoint_recovery(self):
        manifest = load_manifest(ROOT / "examples" / "huge-sharded" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runpod-execution" / "logs").mkdir(parents=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("failed\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "failed", "exit_code": 2}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"ts":"2026-04-30T00:00:00Z","phase":"x","status":"failed"}\n')
            (base / "runpod-execution" / "checkpoints").mkdir(parents=True)
            (base / "runpod-execution" / "checkpoints" / "step.json").write_text("{}")
            manifest["workload"]["checkpoint_policy"]["path"] = "runpod-execution/checkpoints"
            report = supervise_execution(manifest, base)
            self.assertEqual(report["action"], "recover_from_checkpoint")

    def test_orchestrator_scan_and_issue_intake_prepare_handoff(self):
        manifest = load_manifest(ROOT / "examples" / "proxy-matrix" / "launch_manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "launch_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
            intake = issue_intake(ROOT / "examples" / "proxy-matrix" / "linear_issue.md", manifest_path, base / "intake")
            self.assertTrue(Path(intake["handoff_path"]).is_file())
            scan = scan_handoffs(base)
            self.assertEqual(len(scan["handoffs"]), 1)
            self.assertEqual(len(scan["blocked"]), 1)

    def test_linear_issue_helpers_extract_identifier(self):
        self.assertEqual(issue_identifier("https://linear.app/acme/issue/ABC-123/title"), "ABC-123")
        markdown = issue_to_markdown({"identifier": "ABC-123", "title": "Run bridge", "description": "Body", "url": "https://linear.app/x"})
        self.assertIn("ABC-123", markdown)
        self.assertIn("Body", markdown)

    def test_recovery_and_dashboard_summarize_run_records(self):
        record = {
            "action": "run_remote",
            "manifest_run_id": "needs-cleanup",
            "status": "verification_error",
            "create": {"pod_id": "pod-123", "pod": {"name": "needs-cleanup", "costPerHr": 0.06}},
            "cleanup": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            record_path = base / "remote_run_record.json"
            record_path.write_text(json.dumps(record))
            recovery = analyze_recovery(record_path)
            self.assertIn("cleanup_pod", recovery["actions"])
            dashboard_records = scan_dashboard_records(base)
            self.assertEqual(dashboard_records[0]["run_id"], "needs-cleanup")
            dashboard = write_dashboard(dashboard_records, base / "dashboard.html")
            self.assertTrue(Path(dashboard["html"]).is_file())

    def test_manifest_requires_exposed_port_for_inspection_server(self):
        manifest = load_manifest(ROOT / "examples" / "proxy-matrix" / "launch_manifest.json")
        manifest["runpod"]["ports"] = []
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "runpod.ports" for issue in result.errors))

    def test_manifest_rejects_invalid_terminal_hold_mode(self):
        manifest = load_manifest(ROOT / "examples" / "public-smoke" / "launch_manifest.json")
        manifest["startup"]["terminal_hold"] = {"mode": "forever-ish"}
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "startup.terminal_hold.mode" for issue in result.errors))

    def test_proxy_matrix_linear_issue_validates(self):
        result = validate_issue_file(ROOT / "examples" / "proxy-matrix" / "linear_issue.md")
        self.assertTrue(result.ok, result)

    def test_public_audit_passes(self):
        report = run_public_audit(ROOT)
        self.assertEqual(report["overall"], "pass", report)

    def test_manifest_audit_catches_stale_launch_bundle(self):
        stale = {
            "schema_version": 1,
            "sidecar_kind": "runpod",
            "stage_contract": {"claim_level": "public_synthetic_demo"},
            "expected_outputs": [{"path": "out.txt"}],
            "provider_handoff_policy": {"mode": "manual"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            (root / "templates" / "sidecar-runpod-launch-bundle.json").write_text(json.dumps(stale))
            report = audit_manifest_tree(root, migration_hints=True)
            self.assertFalse(report["ok"])
            self.assertEqual(report["summary"]["manifest_candidates"], 1)
            issues = report["results"][0]
            self.assertFalse(issues["ok"])
            self.assertTrue(any(issue["path"] == "manifest_kind" for issue in issues["errors"]))
            self.assertTrue(any(issue["path"] == "stage_contract" for issue in issues["warnings"]))
            self.assertTrue(any("move top-level stage_contract" in hint for hint in issues["migration_hints"]))
            self.assertTrue(any("expected_outputs" in hint for hint in issues["migration_hints"]))

    def test_manifest_audit_gpu_type_hint_only_for_gpu_manifests(self):
        cpu_hints = build_migration_hints({"runpod": {"gpuCount": 0, "gpuTypeIds": []}})
        self.assertFalse(any("avoid empty gpuTypeIds" in hint for hint in cpu_hints))
        gpu_hints = build_migration_hints({"runpod": {"gpuCount": 1, "gpuTypeIds": []}})
        self.assertTrue(any("avoid empty gpuTypeIds" in hint for hint in gpu_hints))

    def test_cli_audit_manifests_prints_migration_hints(self):
        stale = {
            "schema_version": 1,
            "sidecar_kind": "runpod",
            "stage_contract": {"claim_level": "public_synthetic_demo"},
            "expected_outputs": [{"path": "out.txt"}],
            "provider_handoff_policy": {"mode": "manual"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "launch_manifest.json").write_text(json.dumps(stale))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["audit-manifests", str(root), "--migration-hints"])
            self.assertEqual(rc, 1)
            text = stdout.getvalue()
            self.assertIn("MIGRATE move top-level stage_contract", text)

    def test_cli_audit_manifests_summary_only_groups_repeated_issues(self):
        stale = {
            "schema_version": 1,
            "sidecar_kind": "runpod",
            "stage_contract": {"claim_level": "public_synthetic_demo"},
            "expected_outputs": [{"path": "out.txt"}],
            "provider_handoff_policy": {"mode": "manual"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("alpha", "beta"):
                manifest_dir = root / name
                manifest_dir.mkdir()
                (manifest_dir / "launch_manifest.json").write_text(json.dumps(stale))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["audit-manifests", str(root), "--migration-hints", "--summary-only"])
            self.assertEqual(rc, 1)
            text = stdout.getvalue()
            self.assertIn("top_errors:", text)
            self.assertIn("2x access: required field is missing", text)
            self.assertIn("top_migration_hints:", text)
            self.assertIn("2x move top-level stage_contract", text)
            self.assertIn("failure_paths:", text)

    def test_audit_runpod_ops_flags_direct_mutation_and_split_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "spawn.py").write_text(
                "RUNPOD_KEY = subprocess.check_output(['jq','-r','.mcpServers.runpod.env.RUNPOD_API_KEY','~/.claude.json'])\n"
                "cmd = 'curl -sS -X DELETE https://rest.runpod.io/v1/pods/pod-123'\n"
            )
            (root / "README.md").write_text(
                "runpod-bridge create-pod demo/launch_manifest.json --execute\n"
                "runpod-bridge cleanup-pod pod-123 --execute\n"
            )
            report = audit_runpod_ops_tree(root)
            self.assertFalse(report["ok"])
            rules = {finding["rule"] for finding in report["findings"]}
            self.assertIn("runpod_key_from_local_app_config", rules)
            self.assertIn("direct_runpod_rest_mutation", rules)
            self.assertIn("split_create_verify_cleanup_recipe", rules)

    def test_cli_audit_runpod_ops_summary_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.sh").write_text("curl -sS -X POST https://rest.runpod.io/v1/pods\n")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["audit-runpod-ops", str(root), "--summary-only"])
            self.assertEqual(rc, 1)
            text = stdout.getvalue()
            self.assertIn("top_findings:", text)
            self.assertIn("direct_runpod_rest_mutation", text)


if __name__ == "__main__":
    unittest.main()
