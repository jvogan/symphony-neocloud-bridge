"""fal.ai serverless-inference provider setup entry.

Setup guidance only. Researched from fal.ai docs (2026-06); NOT yet run through
this bridge.

The fal queue is the cleanest stateless submit->poll->retrieve primitive in the
neocloud survey - nothing to tear down on the default path. The trap is that
output artifact URLs are PUBLIC by default.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class FalAdapter(ProviderAdapter):
    name = "fal"
    adapter_id = "fal_queue_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "fal.ai serverless inference (fastest gen-media). Its QUEUE is the cleanest stateless primitive "
        "surveyed: POST queue.fal.run/{model} -> request_id (+status/response/cancel urls) -> poll -> GET "
        "result; the queue wait is FREE and there is nothing to tear down. Auth: Authorization: Key $FAL_KEY. "
        "Per-request controls X-Fal-Request-Timeout / X-Fal-No-Retry / X-Fal-Object-Lifecycle-Preference. "
        "WARNING: output artifact URLs are PUBLIC by default - set a lifecycle."
    )
    learnings_doc = "docs/providers/neoclouds.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate the model id + input schema and the per-request safety headers before any paid call",
        "render run as POST queue.fal.run/{model} -> poll status -> GET response (or register a "
        "?fal_webhook=); wire artifact fetch + SHA-256",
        "set X-Fal-Request-Timeout (bound), X-Fal-No-Retry (auto-retry is ON by default; set it for a "
        "deterministic single attempt), and X-Fal-Object-Lifecycle-Preference; sanitize outputs because "
        "artifact URLs are public",
        "bound spend per-output / GPU-sec; treat fal as gen-media-first, thinner for arbitrary code",
    ]
    known_patterns = [
        "the queue is the bridge-fit primitive: POST queue.fal.run/{model} -> request_id + "
        "status_url/response_url/cancel_url -> poll (IN_QUEUE/IN_PROGRESS/COMPLETED) -> GET response; webhook "
        "via ?fal_webhook=",
        "the queue wait is free and jobs self-complete - managed_inference, nothing to tear down on the "
        "default path",
        "auth via the header Authorization: Key $FAL_KEY",
        "per-request safety headers: set X-Fal-Request-Timeout (bound), X-Fal-No-Retry (auto-retry is ON by "
        "default, up to ~10x; failed 5xx attempts aren't billed and retries share one deadline - set it for a "
        "deterministic single-attempt canary), and X-Fal-Object-Lifecycle-Preference",
        "ARTIFACTS ARE PUBLIC by default - sanitize outputs and set an object lifecycle before any "
        "private/production use; treat fal artifact URLs as world-readable",
        "billing per-output (e.g. Flux ~$0.04/img) or GPU-sec (H100 ~$1.89/hr); gen-media-centric, thinner "
        "for arbitrary code; output-size limits undocumented",
    ]

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": self.adapter_id,
            "automated_launch": False,
            "category": self.category,
            "provenance": self.provenance,
            "summary": self.summary,
            "learnings_doc": self.learnings_doc,
            "portable_contract": list(PORTABLE_CONTRACT),
            "known_patterns": self.known_patterns,
            "roadmap": self.roadmap,
        }
