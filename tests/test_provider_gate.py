import tempfile
import unittest
from pathlib import Path

from cloud_bridge.providers.base import ProviderLaunchUnsupported
from cloud_bridge.providers.runpod.rest import RunpodRestError
from cloud_bridge.remote_run import assert_provider_launch_supported, cleanup_orphan_pods, run_remote_flow


class GateClient:
    """Records whether any pod-touching API call happened."""

    def __init__(self):
        self.touched = False

    def list_pods(self, name=None):
        self.touched = True
        return []

    def create_pod(self, body):
        self.touched = True
        return {"id": "x"}


class ProviderGateTests(unittest.TestCase):
    def test_assert_launch_supported_allows_runpod_and_default(self):
        assert_provider_launch_supported({"provider": {"name": "runpod"}})  # automated_launch
        assert_provider_launch_supported({})  # defaults to runpod

    def test_assert_launch_supported_blocks_setup_guidance_provider(self):
        with self.assertRaises(ProviderLaunchUnsupported):
            assert_provider_launch_supported({"provider": {"name": "modal"}})

    def test_assert_launch_supported_blocks_unknown_provider(self):
        with self.assertRaises(ProviderLaunchUnsupported):
            assert_provider_launch_supported({"provider": {"name": "nonesuch"}})

    def test_paid_run_on_setup_guidance_provider_blocked_before_any_api_call(self):
        client = GateClient()
        manifest = {"provider": {"name": "modal"}, "remote_launch_allowed": True}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProviderLaunchUnsupported):
                run_remote_flow(manifest, out_dir=tmp, execute=True, client=client)
        self.assertFalse(client.touched)  # gate fired before list_pods/create_pod


class OrphanDeleteClient:
    def __init__(self):
        self.deleted = []

    def delete_pod(self, pod_id):
        self.deleted.append(pod_id)
        # 404-on-delete is treated as already_absent (the orphan is gone) -> success
        raise RunpodRestError("not found", status_code=404)


class OrphanCleanupTests(unittest.TestCase):
    def test_cleanup_orphan_pods_deletes_each_candidate(self):
        client = OrphanDeleteClient()
        with tempfile.TemporaryDirectory() as tmp:
            result = cleanup_orphan_pods(
                [{"id": "orphan-1"}, {"id": "orphan-2"}],
                out_dir=Path(tmp),
                client=client,
                wait=False,
                timeout_seconds=5,
                interval_seconds=1,
            )
        self.assertEqual(client.deleted, ["orphan-1", "orphan-2"])  # the S2 orphans get deleted, not left billing
        self.assertEqual(result["status"], "orphans_deleted")
        self.assertEqual(result["run_status"], "created_missing_pod_id_cleaned")

    def test_cleanup_orphan_pods_no_candidates(self):
        result = cleanup_orphan_pods([], out_dir=Path("/tmp"), client=OrphanDeleteClient(), wait=False, timeout_seconds=5, interval_seconds=1)
        self.assertEqual(result["status"], "no_orphans_found")


if __name__ == "__main__":
    unittest.main()
