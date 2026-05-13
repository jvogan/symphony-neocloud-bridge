import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from runpod_bridge.aws_orchestration import build_aws_orchestrator_plan
from runpod_bridge.bootstrap_requirements import bootstrap_requirements_report
from runpod_bridge.cli import main as cli_main
from runpod_bridge.closeout import write_closeout_files
from runpod_bridge.contract import contract_self_check
from runpod_bridge.cost import cost_report_from_record
from runpod_bridge.dashboard import scan_dashboard_records, write_dashboard
from runpod_bridge.egress import build_egress_plan
from runpod_bridge.handoff import (
    run_handoff_flow,
    validate_provider_handoff,
    write_provider_handoff,
)
from runpod_bridge.linear_issue import validate_issue_file
from runpod_bridge.linear_api import issue_identifier, issue_to_markdown
from runpod_bridge.local_run import run_local
from runpod_bridge.manifest import build_plan, load_manifest, validate_manifest
from runpod_bridge.monitor import inspect_execution
from runpod_bridge.orchestrator import issue_intake, scan_handoffs
from runpod_bridge.packet import prepare_packet
from runpod_bridge.payload import MAX_POST_BODY_BYTES, create_request_payload_report
from runpod_bridge.preflight import analyze_preflight
from runpod_bridge.productivity import build_productivity_plan
from runpod_bridge.profiles import get_profile, recommend_profile
from runpod_bridge.providers import provider_capabilities
from runpod_bridge.proxy import proxy_url, required_proxy_paths, tcp_endpoint_from_pod, tcp_url
from runpod_bridge.public_readiness import (
    find_disallowed_release_paths,
    run_public_audit,
    scan_for_forbidden_text,
    validate_readme_sections,
)
from runpod_bridge.recovery import analyze_recovery
from runpod_bridge.remote_run import acquire_launch_lock, release_launch_lock, run_remote_flow
from runpod_bridge.runpod_rest import (
    RunpodRestError,
    active_duplicate_pods,
    build_create_pod_request,
    cleanup_pod_flow,
    create_pod_flow,
)
from runpod_bridge.runpod_runtime import analyze_runtime_metrics, build_runtime_metrics_report
from runpod_bridge.runpodctl import billing_pods_command, build_pod_create_command, shell_join
from runpod_bridge.source_check import check_source_reachability
from runpod_bridge.startup import render_startup_script
from runpod_bridge.supervisor import supervise_execution


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
        manifest["runpod"]["env"] = {"API_KEY": "literal-secret-value"}
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
        report = bootstrap_requirements_report(manifest)
        self.assertTrue(report["ok"], report)

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
        self.assertIn("git checkout", script)
        self.assertIn('cd "${RUNPOD_REPO_DIR:-/workspace/repo}"', script)
        self.assertIn("RUNPOD_LOG_FILE=\"${RUNPOD_LOG_FILE:-runpod-execution/logs/startup.log}\"", script)

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
        self.assertIn("curl --fail --silent --show-error --upload-file", script)
        self.assertIn('destination="aws-s3-presigned-upload"', script)

    def test_aws_s3_presigned_upload_rejects_literal_url(self):
        manifest = copy.deepcopy(self.manifest())
        manifest["artifact_egress"].update(
            {
                "mode": "aws_s3_presigned_upload",
                "archive_upload_url": "https://s3.amazonaws.com/public-bucket/object?X-Amz-Signature=redacted",
                "requires_presigned_upload": True,
            }
        )
        result = validate_manifest(manifest)
        self.assertFalse(result.ok)
        self.assertTrue(any("bearer credentials" in issue.message for issue in result.errors))

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

            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "succeeded")
            self.assertTrue((base / "runpod-execution" / "closeout.json").is_file())
            self.assertTrue(closeout["artifacts"][0]["sha256"])

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

            closeout = write_closeout_files(manifest, base)
            self.assertEqual(closeout["status"], "failed")
            self.assertEqual(closeout["forbidden_artifact_markers"][0]["markers"], ["mock"])

    def test_monitor_reads_local_execution_packet(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runpod-execution" / "logs").mkdir(parents=True)
            (base / "runpod-execution" / "logs" / "startup.log").write_text("running\n")
            (base / "runpod-execution" / "status.json").write_text(json.dumps({"status": "running", "exit_code": 0}))
            (base / "runpod-execution" / "monitor_events.ndjson").write_text('{"ts":"2999-01-01T00:00:00Z","phase":"heartbeat","status":"running"}\n')

            report = inspect_execution(manifest, base)
            self.assertEqual(report["state"], "running")
            self.assertTrue(report["files"]["log_present"])
            self.assertEqual(report["productivity"]["state"], "productive")
            self.assertTrue(report["productivity"]["productive"])

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
        with tempfile.TemporaryDirectory() as tmp:
            record = create_pod_flow(manifest, out_dir=tmp, execute=True, client=FailingCreateClient())
            self.assertEqual(record["status"], "failed_create_request")
            self.assertIn("creation state is unknown", record["blockers"][0])
            saved = json.loads((Path(tmp) / "runpod_resource_record.json").read_text())
            self.assertEqual(saved["duplicate_check"]["active_matches"], [])

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

    def test_run_remote_flow_verifies_and_cleans_up_created_pod(self):
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
                "closeout": {"status": "succeeded", "artifacts": [{"artifact_id": "result"}]},
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
            self.assertEqual(record["cleanup"]["status"], "submitted")
            self.assertTrue(record["launch_lock"]["released"])
            self.assertFalse(list((base / "locks").glob("*.lock.json")))
            self.assertEqual(client.deleted, ["pod-123"])
            saved = json.loads((base / "run" / "remote_run_record.json").read_text())
            self.assertEqual(saved["verification"]["closeout"]["status"], "succeeded")
            self.assertTrue(saved["launch_lock"]["released"])

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
            self.assertEqual(record["cleanup"]["status"], "submitted")
            self.assertEqual(client.deleted, ["pod-123"])

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
            self.assertEqual(record["cleanup"]["status"], "submitted")
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

        def verifier(mode, manifest, pod_id, api, port, out_dir, timeout_seconds, interval_seconds):
            return {"ok": True, "mode": mode, "pod_id": pod_id, "closeout": {"status": "succeeded"}}

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
        self.assertTrue(capabilities["implemented"])
        self.assertIn("billing", capabilities["resources"])
        self.assertIn("flash", capabilities["resources"])
        self.assertIn("runtime_metrics", capabilities["resources"])
        self.assertIn("serverless_endpoints", capabilities["resources"])
        self.assertIn("aws_integrations", capabilities["resources"])
        self.assertIn("runtime-metrics", capabilities["resources"]["pods"]["bridge_commands"])
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

    def test_proxy_matrix_linear_issue_validates(self):
        result = validate_issue_file(ROOT / "examples" / "proxy-matrix" / "linear_issue.md")
        self.assertTrue(result.ok, result)

    def test_public_audit_passes(self):
        report = run_public_audit(ROOT)
        self.assertEqual(report["overall"], "pass", report)

    def test_public_text_scan_covers_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "src").mkdir()
            leaked_path = "/" + "Users/example/private"
            (base / "src" / "leak.py").write_text(f'path = "{leaked_path}"\n')
            hits = scan_for_forbidden_text(base)
            self.assertTrue(any(hit["path"] == "src/leak.py" for hit in hits), hits)

    def test_public_path_scan_blocks_generated_artifacts_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runpod-execution").mkdir()
            (base / "runpod-execution" / "status.json").write_text("{}")
            hits = find_disallowed_release_paths(base)
            self.assertTrue(any(hit["path"] == "runpod-execution/status.json" for hit in hits), hits)

    def test_public_readme_scan_requires_release_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "README.md").write_text("# Example\n")
            gaps = validate_readme_sections(base)
            self.assertTrue(any(gap["heading"] == "## Quick Start" for gap in gaps), gaps)


if __name__ == "__main__":
    unittest.main()
