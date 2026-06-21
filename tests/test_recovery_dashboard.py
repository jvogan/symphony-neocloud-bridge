from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from cloud_bridge.dashboard import scan_dashboard_records, summarize_dashboard, write_dashboard
from cloud_bridge.recovery import analyze_recovery, recover_run
from cloud_bridge.providers.runpod.rest import RunpodRestError


class RecoveryDashboardTests(unittest.TestCase):
    def test_cleanup_submitted_is_unverified_risk_with_commands(self):
        record = {
            "action": "run_remote",
            "ts": "2026-04-30T22:00:00Z",
            "manifest_run_id": "cleanup-submitted",
            "status": "succeeded",
            "create": {
                "pod_id": "pod-123",
                "pod": {
                    "id": "pod-123",
                    "name": "cleanup-submitted",
                    "costPerHr": 0.06,
                    "lastStartedAt": "2026-04-30T21:58:00Z",
                },
            },
            "verification": {"ok": True},
            "cleanup": {"status": "submitted", "action": "delete", "ts": "2026-04-30T22:00:00Z"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "remote_run_record.json"
            path.write_text(json.dumps(record))

            analysis = analyze_recovery(path)
            self.assertEqual(analysis["risk"], "high")
            self.assertFalse(analysis["cleanup_verified"])
            self.assertTrue(analysis["cleanup_unverified"])
            self.assertIn("verify_cleanup", analysis["actions"])
            self.assertTrue(any("get-pod pod-123" in command for command in analysis["recommended_commands"]))

            records = scan_dashboard_records(base)
            summary = summarize_dashboard(records)
            self.assertEqual(summary["high_risk"], 1)
            self.assertEqual(summary["cleanup_unverified"], 1)
            self.assertEqual(summary["open_cost_per_hr"], 0.06)
            self.assertGreater(summary["estimated_cost_usd"], 0)

            dashboard = write_dashboard(records, base / "dashboard.html")
            self.assertEqual(dashboard["summary"]["cleanup_unverified"], 1)
            html = Path(dashboard["html"]).read_text()
            self.assertIn("Estimated Total", html)
            self.assertIn("Recommended", html)

    def test_stale_launch_lock_is_flagged_from_record(self):
        record = {
            "action": "run_remote",
            "manifest_run_id": "blocked-lock",
            "status": "blocked_launch_lock",
            "launch_lock": {
                "status": "held",
                "acquired": False,
                "key": "blocked-lock",
                "path": "/tmp/blocked-lock.lock.json",
                "existing": {
                    "ts": "2020-01-01T00:00:00Z",
                    "owner_id": "old-owner",
                    "pid": 12345,
                    "run_id": "blocked-lock",
                    "out_dir": "/tmp/old-run",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote_run_record.json"
            path.write_text(json.dumps(record))

            analysis = analyze_recovery(path)
            self.assertEqual(analysis["risk"], "medium")
            self.assertTrue(analysis["lock"]["stale"])
            self.assertIn("inspect_stale_launch_lock", analysis["actions"])
            self.assertTrue(any("cat /tmp/blocked-lock.lock.json" in command for command in analysis["recommended_commands"]))

    def test_raw_resource_record_with_created_pod_recommends_cleanup(self):
        record = {
            "action": "create_pod",
            "ts": "2026-04-30T22:00:00Z",
            "status": "created",
            "response": {
                "id": "pod-raw",
                "name": "raw-create",
                "costPerHr": 0.12,
                "lastStartedAt": "2026-04-30T21:55:00Z",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runpod_resource_record.json"
            path.write_text(json.dumps(record))

            analysis = analyze_recovery(path)
            self.assertEqual(analysis["risk"], "high")
            self.assertIn("cleanup_pod", analysis["actions"])
            self.assertTrue(any("cleanup-pod pod-raw" in command for command in analysis["recommended_commands"]))

            records = scan_dashboard_records(Path(tmp))
            self.assertEqual(records[0]["pod_id"], "pod-raw")
            self.assertEqual(records[0]["risk"], "high")

    def test_recover_run_reports_already_absent_as_verified(self):
        record = {
            "action": "run_remote",
            "status": "verification_failed",
            "create": {"pod_id": "pod-123", "pod": {"id": "pod-123", "name": "gone"}},
        }

        class MissingClient:
            def delete_pod(self, pod_id):
                raise RunpodRestError("missing", status_code=404)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote_run_record.json"
            path.write_text(json.dumps(record))
            result = recover_run(path, execute_cleanup=True, client=MissingClient())
            self.assertEqual(result["cleanup"]["status"], "already_absent")
            self.assertEqual(result["status"], "cleanup_verified")


if __name__ == "__main__":
    unittest.main()
