import json
import os
from unittest import TestCase
from unittest.mock import patch

from cloud_bridge.launch_env import build_bridge_managed_env
from cloud_bridge.providers.runpod.rest import build_create_pod_request
from cloud_bridge.util import redact
from cloud_bridge.startup import render_startup_script


class SourceArchiveUrlEnvTests(TestCase):
    def manifest(self):
        return {
            "run_id": "source-url-test",
            "remote_launch_allowed": True,
            "budget": {
                "max_runtime_minutes": 10,
                "terminate_after_minutes": 15,
                "max_estimated_cost_usd": 1,
            },
            "repo": {
                "source": "prepared_snapshot",
                "url_or_path": "env:RUNPOD_SOURCE_ARCHIVE_URL",
                "commit_or_snapshot": "sha256:" + "a" * 64,
                "workdir": "/workspace/repo",
                "snapshot": {
                    "archive_url_ref": "env:RUNPOD_SOURCE_ARCHIVE_URL",
                    "archive_sha256": "a" * 64,
                },
            },
            "runpod": {
                "cloudType": "SECURE",
                "imageName": "python:3.12-slim",
                "gpuCount": 0,
                "env": {},
            },
            "startup": {
                "mode": "dockerStartCmd",
                "commands": ["true"],
            },
        }

    def test_env_archive_url_is_injected_from_trusted_orchestrator_env(self):
        signature_param = "X-Amz-" + "Signature=abc"
        url = f"https://example.com/source.tar.gz?{signature_param}"
        with patch.dict(os.environ, {"RUNPOD_SOURCE_ARCHIVE_URL": url}):
            env = build_bridge_managed_env(self.manifest())

        self.assertEqual(env["RUNPOD_SOURCE_ARCHIVE_URL_ENV"], "RUNPOD_SOURCE_ARCHIVE_URL")
        self.assertEqual(env["RUNPOD_SOURCE_ARCHIVE_URL"], url)

    def test_missing_env_archive_url_remains_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            env = build_bridge_managed_env(self.manifest())

        self.assertEqual(env["RUNPOD_SOURCE_ARCHIVE_URL_ENV"], "RUNPOD_SOURCE_ARCHIVE_URL")
        self.assertEqual(env["RUNPOD_SOURCE_ARCHIVE_URL"], "")

    def test_create_request_redacts_injected_archive_url(self):
        signature_param = "X-Amz-" + "Signature=abc"
        url = f"https://example.com/source.tar.gz?{signature_param}"
        with patch.dict(os.environ, {"RUNPOD_SOURCE_ARCHIVE_URL": url}):
            request = build_create_pod_request(self.manifest())

        self.assertEqual(request["env"]["RUNPOD_SOURCE_ARCHIVE_URL"], url)
        redacted = redact(request)
        self.assertEqual(redacted["env"]["RUNPOD_SOURCE_ARCHIVE_URL"], "<redacted>")

    def test_runpod_env_ref_is_resolved_and_redacted(self):
        manifest = self.manifest()
        manifest["runpod"]["env"]["HF_TOKEN"] = "env:HF_TOKEN"

        with patch.dict(os.environ, {"HF_TOKEN": "hf_secret_value"}):
            request = build_create_pod_request(manifest)

        self.assertEqual(request["env"]["HF_TOKEN"], "hf_secret_value")
        redacted = redact(request)
        self.assertEqual(redacted["env"]["HF_TOKEN"], "<redacted>")

    def test_redact_handles_cyclic_progress_payloads(self):
        live_progress = {"log_tail": ["booting", "ready"]}
        report = {
            "classification": {"state": "workload_progressing"},
            "live_progress": live_progress,
        }
        live_progress["self"] = report

        redacted = redact(report)

        self.assertEqual(redacted["live_progress"]["log_tail"], ["booting", "ready"])
        self.assertEqual(redacted["live_progress"]["self"], "<redacted:circular>")
        json.dumps(redacted)

    def test_missing_runpod_env_ref_is_empty(self):
        manifest = self.manifest()
        manifest["runpod"]["env"]["HF_TOKEN"] = "env:HF_TOKEN"

        with patch.dict(os.environ, {}, clear=True):
            request = build_create_pod_request(manifest)

        self.assertEqual(request["env"]["HF_TOKEN"], "")

    def test_prepared_snapshot_download_falls_back_to_python_when_curl_is_missing(self):
        script = render_startup_script(self.manifest())

        self.assertIn("download_source_archive()", script)
        self.assertIn("curl --fail --silent --show-error --location", script)
        self.assertIn("urllib.request", script)
        self.assertIn("curl or python is required for prepared snapshot bootstrap", script)
        self.assertNotIn("curl is required for prepared snapshot bootstrap", script)

    def test_empty_docker_entrypoint_is_preserved_to_clear_image_entrypoint(self):
        manifest = self.manifest()
        manifest["runpod"]["dockerEntrypoint"] = []

        request = build_create_pod_request(manifest)

        self.assertEqual(request["dockerEntrypoint"], [])
