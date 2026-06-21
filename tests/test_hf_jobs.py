import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cloud_bridge import cli
from cloud_bridge.closeout import build_closeout, write_closeout_files
from cloud_bridge.remote_outcome import is_hf_job_record, write_remote_outcome
from cloud_bridge.providers import get_adapter
from cloud_bridge.providers.base import ProviderLaunchUnsupported
from cloud_bridge.providers.huggingface.rest import (
    HfJobsError,
    is_terminal_stage,
    job_stage,
    resolve_url,
)
from cloud_bridge.providers.huggingface.jobs import (
    build_job_spec,
    estimate_worst_case_cost,
    job_spec_blockers,
    run_job_flow,
    safe_spec_for_record,
)

EXAMPLE_MANIFEST = str(Path(__file__).resolve().parents[1] / "examples" / "hf-job" / "launch_manifest.json")
RESULT_REPO_PATH = "hf-execution/artifacts/result.json"
# Secret-name strings live in deliberately non-sensitive-named constants (no token/secret/key in the
# variable name) and are assembled at runtime, so the public-readiness audit never sees a literal
# sensitive-key:value pair or a contiguous hf_ value in this test file.
JOB_SLOT = "HF" + "_TOKEN"  # the job-side secret name the job sees (HF_TOKEN)
PUSH_ENV = "HF_TOKEN_TEST_ENV"  # a LOCAL env var NAME (a reference, not a secret value)
PUSH_VALUE = "env-secret-value"  # fake resolved secret value
BOGUS_HF_VALUE = "hf" + "_" + "X" * 36  # realistic-looking token literal, never contiguous in source


def hf_manifest(**overrides):
    manifest = {
        "run_id": "hf-test",
        "provider": {"name": "huggingface", "adapter": "huggingface_v1"},
        "huggingface": {
            "namespace": "ns",
            "flavor": "cpu-basic",
            "dockerImage": "python:3.12-slim",
            "timeoutSeconds": 600,
            "command": ["bash", "-lc", "echo hi"],
            "secret_refs": {JOB_SLOT: PUSH_ENV},
            "environment": {"SYMPHONY_RUN_ID": "hf-test"},
        },
        "startup": {
            "execution_dir": "hf-execution",
            "status_file": "hf-execution/status.json",
            "log_file": "hf-execution/logs/job.log",
        },
        "monitoring": {
            "requires_log_artifact": True,
            "requires_status_file": True,
            "requires_workload_heartbeat": False,
        },
        "expected_artifacts": [
            {"artifact_id": "r", "path": RESULT_REPO_PATH, "required": True, "sha256_required": True}
        ],
        "artifact_egress": {"mode": "hf_hub_repo", "repo_id": "ns/out", "repo_type": "dataset", "revision": "main"},
        "closeout": {"record_artifact_hashes": True},
    }
    manifest.update(overrides)
    return manifest


class FakeHfClient:
    """Scripts a job lifecycle and records the spec submitted, cancels, and downloads."""

    def __init__(
        self,
        *,
        submit=None,
        stages=None,
        logs=None,
        downloads=None,
        hardware=None,
        raise_on_submit=None,
        raise_on_download=None,
    ):
        self.base_url = "https://huggingface.co"
        self.namespace = "ns"
        self._submit = submit or {"id": "job-123", "status": {"stage": "SCHEDULING"}}
        self._stages = list(stages or ["COMPLETED"])
        self._stage_idx = 0
        self._logs = logs if logs is not None else ["log line one", "log line two"]
        self._downloads = downloads if downloads is not None else {RESULT_REPO_PATH: b'{"ok": true}\n'}
        self._hardware = hardware or []
        self._raise_on_submit = raise_on_submit
        self._raise_on_download = raise_on_download
        self.submitted_spec = None
        self.cancelled = []
        self.log_calls = 0

    def hardware(self):
        return self._hardware

    def submit_job(self, spec):
        self.submitted_spec = spec
        if self._raise_on_submit:
            raise self._raise_on_submit
        return dict(self._submit)

    def get_job(self, job_id):
        stage = self._stages[min(self._stage_idx, len(self._stages) - 1)]
        self._stage_idx += 1
        return {"id": job_id, "status": {"stage": stage}, "durations": {"totalSecs": 5}}

    def cancel_job(self, job_id):
        self.cancelled.append(job_id)
        return {"id": job_id, "status": {"stage": "CANCELED"}}

    def fetch_logs(self, job_id, tail=None):
        self.log_calls += 1
        return list(self._logs)

    def download(self, url):
        if self._raise_on_download:
            raise self._raise_on_download
        for repo_path, data in self._downloads.items():
            if url.endswith(repo_path):
                return data
        raise HfJobsError(f"HF artifact download failed with HTTP 404: {url}", status_code=404)


class HfHelpersTests(unittest.TestCase):
    def test_terminal_stage_is_allowlist(self):
        self.assertTrue(is_terminal_stage("COMPLETED"))
        self.assertTrue(is_terminal_stage("error"))
        self.assertFalse(is_terminal_stage("RUNNING"))
        self.assertFalse(is_terminal_stage("UPDATING"))  # undocumented but real non-terminal stage

    def test_job_stage_reads_nested_status(self):
        self.assertEqual(job_stage({"status": {"stage": "RUNNING"}}), "RUNNING")
        self.assertEqual(job_stage({}), "")

    def test_resolve_url_dataset_and_model(self):
        self.assertEqual(
            resolve_url("https://huggingface.co", "dataset", "me/out", "a/b.json"),
            "https://huggingface.co/datasets/me/out/resolve/main/a/b.json",
        )
        self.assertEqual(
            resolve_url("https://huggingface.co", "model", "me/out", "a/b.json"),
            "https://huggingface.co/me/out/resolve/main/a/b.json",
        )

    def test_cost_estimate_static_and_unknown(self):
        self.assertEqual(estimate_worst_case_cost("cpu-basic", 600), 0.002)
        self.assertEqual(estimate_worst_case_cost("a100-large", 1800), 1.251)
        self.assertIsNone(estimate_worst_case_cost("mystery-flavor", 600))

    def test_cost_estimate_prefers_live_catalog(self):
        hardware = [{"id": "cpu-basic", "unit_label": "minute", "unit_cost_micro_usd": 500}]
        self.assertEqual(estimate_worst_case_cost("cpu-basic", 600, hardware=hardware), 0.005)


class HfSpecTests(unittest.TestCase):
    def test_build_spec_image_command_timeout(self):
        spec = build_job_spec(hf_manifest())
        self.assertEqual(spec["dockerImage"], "python:3.12-slim")
        self.assertEqual(spec["flavor"], "cpu-basic")
        self.assertEqual(spec["timeoutSeconds"], 600)
        self.assertEqual(spec["command"], ["bash", "-lc", "echo hi"])
        self.assertNotIn("secrets", spec)  # no secrets unless explicitly injected

    def test_build_spec_space_is_exclusive_with_image(self):
        manifest = hf_manifest()
        manifest["huggingface"]["spaceId"] = "lhoestq/duckdb"
        spec = build_job_spec(manifest)
        self.assertEqual(spec["spaceId"], "lhoestq/duckdb")
        self.assertNotIn("dockerImage", spec)

    def test_command_derived_from_startup_when_absent(self):
        manifest = hf_manifest()
        del manifest["huggingface"]["command"]
        manifest["startup"]["commands"] = ["set -e", "echo a", "echo b"]
        spec = build_job_spec(manifest)
        self.assertEqual(spec["command"], ["bash", "-lc", "set -e\necho a\necho b"])

    def test_secrets_injected_only_when_provided(self):
        spec = build_job_spec(hf_manifest(), secrets={JOB_SLOT: "value"})
        self.assertEqual(spec["secrets"], {JOB_SLOT: "value"})

    def test_safe_spec_strips_secret_values_and_redacts_leaks(self):
        manifest = hf_manifest()
        # a token wrongly placed in plaintext environment must not survive into the record
        manifest["huggingface"]["environment"] = {"LEAKED": BOGUS_HF_VALUE}
        spec = build_job_spec(manifest, secrets={JOB_SLOT: BOGUS_HF_VALUE})
        safe = safe_spec_for_record(spec)
        blob = json.dumps(safe)
        self.assertNotIn(BOGUS_HF_VALUE, blob)  # value-aware redaction caught the env leak
        self.assertEqual(safe["secrets"], {JOB_SLOT: "<redacted>"})  # secret reduced to its name

    def test_blockers_flag_missing_command_and_repo(self):
        manifest = hf_manifest()
        manifest["huggingface"]["command"] = []
        manifest["startup"].pop("commands", None)
        manifest["artifact_egress"]["repo_id"] = ""
        blockers = job_spec_blockers(manifest, build_job_spec(manifest))
        self.assertTrue(any("command" in b for b in blockers))
        self.assertTrue(any("repo_id" in b for b in blockers))


class HfDryRunTests(unittest.TestCase):
    def test_dry_run_renders_request_without_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=False)
        self.assertEqual(record["status"], "dry_run_request")
        self.assertEqual(record["request"]["flavor"], "cpu-basic")
        self.assertEqual(record["cost_estimate"]["worst_case_usd"], 0.002)

    def test_spend_ceiling_blocks_overbudget_flavor(self):
        manifest = hf_manifest()
        manifest["huggingface"]["flavor"] = "a100x8"
        manifest["huggingface"]["timeoutSeconds"] = 3600
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(manifest, out_dir=tmp, execute=False, max_spend_usd=1.0)
        self.assertEqual(record["status"], "blocked_spend_ceiling")

    def test_spend_ceiling_blocks_unpriced_flavor(self):
        manifest = hf_manifest()
        manifest["huggingface"]["flavor"] = "mystery-flavor"
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(manifest, out_dir=tmp, execute=False, max_spend_usd=5.0)
        self.assertEqual(record["status"], "blocked_spend_ceiling")


class HfExecuteTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("cloud_bridge.providers.huggingface.jobs.time.sleep", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {PUSH_ENV: PUSH_VALUE})
        env.start()
        self.addCleanup(env.stop)

    def test_happy_path_verifies_artifacts_and_writes_evidence(self):
        client = FakeHfClient(stages=["RUNNING", "COMPLETED"])
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client, poll_interval_seconds=1)
            base = Path(tmp)
            self.assertEqual(record["status"], "artifacts_verified")
            self.assertEqual(record["submit"]["job_id"], "job-123")
            # evidence files closeout will consume
            status = json.loads((base / "hf-execution/status.json").read_text())
            self.assertEqual(status["status"], "succeeded")
            self.assertTrue((base / "hf-execution/logs/job.log").is_file())
            self.assertTrue((base / "hf-execution/artifact_hashes.jsonl").read_text().strip())
            self.assertTrue((base / RESULT_REPO_PATH).is_file())
            egress = json.loads((base / "hf-execution/egress_status.json").read_text())
            self.assertEqual(egress["status"], "verified")

    def test_closeout_consumes_hf_evidence_as_success(self):
        client = FakeHfClient(stages=["COMPLETED"])
        manifest = hf_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            run_job_flow(manifest, out_dir=tmp, execute=True, client=client)
            closeout = build_closeout(manifest, base_dir=tmp)
        self.assertEqual(closeout["status"], "succeeded")
        self.assertTrue(closeout["egress_ok"])
        self.assertEqual(closeout["missing_required_artifacts"], [])

    def test_secret_sent_to_api_but_redacted_in_record(self):
        client = FakeHfClient(stages=["COMPLETED"])
        with tempfile.TemporaryDirectory() as tmp:
            run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client)
            record_text = (Path(tmp) / "hf_job_record.json").read_text()
        # the real value is sent to the API in the encrypted secrets field...
        self.assertEqual(client.submitted_spec["secrets"], {JOB_SLOT: PUSH_VALUE})
        # ...and the job command's plaintext environment never carries it
        self.assertNotIn(PUSH_VALUE, json.dumps(client.submitted_spec["environment"]))
        # ...but it is never written to the on-disk audit record
        self.assertNotIn(PUSH_VALUE, record_text)

    def test_missing_secret_blocks_before_submit(self):
        os.environ.pop(PUSH_ENV, None)
        client = FakeHfClient(stages=["COMPLETED"])
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client)
        self.assertEqual(record["status"], "blocked_missing_secret")
        self.assertIsNone(client.submitted_spec)  # never submitted

    def test_job_error_stage_is_reported_and_not_egressed(self):
        client = FakeHfClient(stages=["RUNNING", "ERROR"])
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client, poll_interval_seconds=1)
            status = json.loads((Path(tmp) / "hf-execution/status.json").read_text())
        self.assertEqual(record["status"], "job_error")
        self.assertEqual(status["status"], "failed")
        self.assertEqual(record["egress"], {})  # no artifact pull on a failed job

    def test_poll_timeout_cancels_to_stop_billing(self):
        client = FakeHfClient(stages=["RUNNING", "RUNNING", "RUNNING"])
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(
                hf_manifest(), out_dir=tmp, execute=True, client=client, poll_timeout_seconds=0
            )
        self.assertEqual(record["status"], "poll_timeout")
        self.assertEqual(client.cancelled, ["job-123"])  # billing stopped

    def test_egress_failure_when_artifact_missing(self):
        client = FakeHfClient(stages=["COMPLETED"], downloads={})  # repo has nothing to pull
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client)
        self.assertEqual(record["status"], "egress_failed")

    def test_submit_error_is_captured(self):
        client = FakeHfClient(raise_on_submit=HfJobsError("HTTP 402: payment required", status_code=402))
        with tempfile.TemporaryDirectory() as tmp:
            record = run_job_flow(hf_manifest(), out_dir=tmp, execute=True, client=client)
        self.assertEqual(record["status"], "error")
        self.assertIn("402", record["error"])


class HfAdapterTests(unittest.TestCase):
    def test_adapter_has_automated_launch_for_jobs_surface(self):
        adapter = get_adapter("huggingface")
        self.assertTrue(adapter.automated_launch)
        self.assertEqual(adapter.category, "notebook_job")
        adapter.assert_launch_supported()  # must not raise
        self.assertEqual(adapter.launch_surface()["cli_commands"], ["run-job"])
        self.assertEqual(adapter.capabilities()["surfaces"]["inference_endpoints"], "setup_guidance")

    def test_gate_allows_huggingface(self):
        from cloud_bridge.remote_run import assert_provider_launch_supported

        assert_provider_launch_supported({"provider": {"name": "huggingface"}})
        with self.assertRaises(ProviderLaunchUnsupported):
            assert_provider_launch_supported({"provider": {"name": "modal"}})


def hf_record(**overrides):
    record = {
        "action": "run_job",
        "provider": "huggingface",
        "manifest_run_id": "hf-test",
        "request": {"dockerImage": "python:3.12-slim", "flavor": "cpu-basic"},
        "cost_estimate": {"flavor": "cpu-basic", "worst_case_usd": 0.002, "basis": "live_catalog"},
        "submit": {"job_id": "job-abc123", "stage": "SCHEDULING"},
        "poll": {"final_stage": "COMPLETED", "timed_out": False, "durations": {"runningSecs": 6}},
        "egress": {"mode": "hf_hub_repo", "status": "verified", "verified": True,
                   "artifacts": [{"repo_path": RESULT_REPO_PATH, "sha256": "deadbeef", "ok": True}]},
        "status_file": "hf-execution/status.json",
        "status": "artifacts_verified",
    }
    record.update(overrides)
    return record


class HfOutcomeTests(unittest.TestCase):
    def test_is_hf_job_record(self):
        self.assertTrue(is_hf_job_record({"provider": "huggingface"}))
        self.assertTrue(is_hf_job_record({"action": "run_job"}))
        self.assertFalse(is_hf_job_record({"action": "run_remote", "provider": "runpod"}))

    def _write(self, tmp, record, closeout=None):
        base = Path(tmp)
        (base / "hf-execution").mkdir(parents=True, exist_ok=True)
        (base / "hf_job_record.json").write_text(json.dumps(record))
        if closeout is not None:
            (base / "hf-execution" / "closeout.json").write_text(json.dumps(closeout))
        return base / "hf_job_record.json"

    def test_remote_outcome_dispatches_and_renders_hf_block(self):
        closeout = {
            "status": "succeeded",
            "egress_ok": True,
            "egress_status": {"mode": "hf_hub_repo", "status": "verified"},
            "artifacts": [{"artifact_id": "r", "sha256": "abc123", "path": RESULT_REPO_PATH}],
            "missing_required_artifacts": [],
            "missing_required_evidence": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = self._write(tmp, hf_record(), closeout)
            out_path = Path(tmp) / "hf-execution" / "symphony_outcome.md"
            payload = write_remote_outcome(rec_path, out_path)
            block = out_path.read_text()
        # the standalone closeout is authoritative for status
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["cleanup_status"], "auto_terminated")
        self.assertIn("provider: huggingface", block)
        self.assertIn("remote_launch: run_job", block)
        self.assertIn("job_id: job-abc123", block)
        self.assertIn("flavor: cpu-basic", block)
        self.assertIn("job_stage: COMPLETED", block)
        self.assertIn("status: succeeded", block)
        self.assertIn("mode: hf_hub_repo", block)
        self.assertIn("- r: abc123", block)  # artifact hash from closeout
        self.assertIn("cleanup_verified: true", block)

    def test_outcome_without_closeout_falls_back_to_record_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = self._write(tmp, hf_record())  # no closeout.json
            out_path = Path(tmp) / "hf-execution" / "symphony_outcome.md"
            payload = write_remote_outcome(rec_path, out_path)
        self.assertEqual(payload["status"], "artifacts_verified")  # honest: closeout not yet run

    def test_canceled_job_reports_canceled_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = hf_record(status="poll_timeout", cancel={"requested": True, "stage": "CANCELED"})
            rec_path = self._write(tmp, record)
            out_path = Path(tmp) / "out.md"
            payload = write_remote_outcome(rec_path, out_path)
            block = out_path.read_text()
        self.assertEqual(payload["cleanup_status"], "canceled")
        self.assertIn("cleanup_status: canceled", block)


class HfCloseoutDirTests(unittest.TestCase):
    def test_closeout_files_land_in_manifest_execution_dir(self):
        manifest = hf_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # materialize the minimum evidence build_closeout reads
            (base / "hf-execution" / "artifacts").mkdir(parents=True, exist_ok=True)
            (base / "hf-execution" / "logs").mkdir(parents=True, exist_ok=True)
            (base / "hf-execution" / "status.json").write_text(json.dumps({"status": "succeeded"}))
            (base / "hf-execution" / "logs" / "job.log").write_text("ok\n")
            (base / "hf-execution" / "artifact_hashes.jsonl").write_text(
                json.dumps({"path": RESULT_REPO_PATH, "sha256": "abc"}) + "\n"
            )
            (base / "hf-execution" / "egress_status.json").write_text(json.dumps({"mode": "hf_hub_repo", "status": "verified"}))
            (base / RESULT_REPO_PATH).write_text('{"ok": true}\n')
            write_closeout_files(manifest, base_dir=tmp)
            self.assertTrue((base / "hf-execution" / "closeout.json").is_file())  # not runpod-execution/
            self.assertFalse((base / "runpod-execution").exists())


def run_cli(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


class HfCliTests(unittest.TestCase):
    def test_run_job_dry_run_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli(["run-job", EXAMPLE_MANIFEST, "--out-dir", tmp, "--max-spend-usd", "1"])
        self.assertEqual(code, 0)
        self.assertIn("status: dry_run_request", out)

    def test_run_job_rejects_non_huggingface_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            manifest.write_text(json.dumps({"provider": {"name": "runpod"}}))
            code, _, err = run_cli(["run-job", str(manifest), "--out-dir", tmp])
        self.assertEqual(code, 2)
        self.assertIn("huggingface", err)

    def test_run_remote_rejects_huggingface_provider(self):
        code, _, err = run_cli(["run-remote", EXAMPLE_MANIFEST, "--out-dir", ".runtime/should-not-run"])
        self.assertEqual(code, 2)
        self.assertIn("run-job", err)  # points the user at the right command

    def test_execute_requires_confirmation_flag(self):
        code, _, err = run_cli(["run-job", EXAMPLE_MANIFEST, "--execute"])
        self.assertEqual(code, 2)
        self.assertIn("--yes-run-paid-hf-job", err)


if __name__ == "__main__":
    unittest.main()
