"""RunPod provider entry with automated launch support for pod rental."""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import ProviderAdapter
from cloud_bridge.providers.capabilities import provider_capabilities


class RunpodAdapter(ProviderAdapter):
    name = "runpod"
    adapter_id = "runpod_pod_v1"
    automated_launch = True
    provenance = "automated launch support through this bridge's guarded CLI"
    summary = "Pod lifecycle via RunPod REST + GraphQL with guarded launch, monitoring, artifact, and cleanup commands."
    learnings_doc = "docs/providers/runpod.md"
    roadmap = ["runpod_flash_v1", "runpod_serverless_v1", "runpod_cluster_v1"]

    def capabilities(self) -> dict[str, Any]:
        caps = dict(provider_capabilities("runpod"))
        caps.setdefault("summary", self.summary)
        caps.setdefault("learnings_doc", self.learnings_doc)
        caps.setdefault("provenance", self.provenance)
        return caps

    def launch_surface(self) -> dict[str, Any]:
        return {
            "automated_launch": True,
            "cli_commands": [
                "run-remote",
                "run-handoff",
                "create-pod",
                "list-pods",
                "get-pod",
                "gpu-catalog",
                "runtime-metrics",
                "progress-report",
                "cleanup-pod",
                "billing-pods",
                "cost-report",
            ],
            "note": "RunPod runs are driven through these guarded CLI commands.",
        }

    def client(self, **kwargs: Any) -> Any:
        """Construct a RunPod REST client. Imported lazily to keep import cheap."""
        from cloud_bridge.providers.runpod.rest import RunpodRestClient

        return RunpodRestClient(**kwargs)
