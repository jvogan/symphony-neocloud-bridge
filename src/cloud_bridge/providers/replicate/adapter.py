"""Replicate (replicate.com) provider setup entry.

Setup guidance only. Researched from replicate.com docs (2026-06); NOT yet run
through this bridge.

Run any model via API (Cog). Plain predictions self-terminate; deployments and
the 1-hour artifact expiry are the traps to encode.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class ReplicateAdapter(ProviderAdapter):
    name = "replicate"
    adapter_id = "replicate_prediction_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "Replicate runs any model via API (Cog). Async REST: POST a prediction -> poll status or webhook. "
        "Plain predictions SELF-TERMINATE (30-min hard auto-timeout) so there is usually nothing to tear "
        "down - but 'deployments' with min_instances>=1 bill until set to 0 / deleted, and prediction output "
        "artifacts are AUTO-DELETED after 1 HOUR. Token auth (REPLICATE_API_TOKEN). Cloudflare announced "
        "acquiring Replicate (Nov 2025, expected to close early 2026) - the integration is fast-moving."
    )
    learnings_doc = "docs/providers/neoclouds.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate the model + input schema and choose prediction (self-terminating) vs deployment "
        "(persistent) before any paid call",
        "render run as POST create-prediction -> poll status or register a webhook; wire artifact fetch + "
        "SHA-256 WITHIN the 1-hour retention window",
        "if a deployment is used, attach the compute_rental teardown contract: record it and set "
        "min_instances=0 / delete at closeout",
        "bound spend with hardware-seconds + the 30-min auto-timeout; re-verify endpoints/pricing against the "
        "shifting Cloudflare integration",
    ]
    known_patterns = [
        "async REST: POST create-prediction -> poll status (starting/processing/succeeded/failed) or use a "
        "webhook; auth via REPLICATE_API_TOKEN",
        "plain predictions self-terminate and timeout at 30 min by default (extendable via the Cancel-After "
        "header, 5s-24h) - a useful built-in bound, but the default can kill a long bio job; size the work or "
        "raise Cancel-After",
        "ARTIFACTS EXPIRE: prediction output files are deleted ~1 hour after completion - fetch + hash "
        "promptly (or use a webhook), never lazily",
        "cleanup axis: predictions need none; a `deployment` with min_instances>=1 is compute_rental that "
        "bills until you set min_instances=0 or delete it - record and tear down deployments",
        "billing is hardware-seconds (e.g. A100-80GB ~$0.0014/s) or per-output; expensive at sustained scale "
        "vs a rented GPU",
        "Cloudflare announced acquiring Replicate (Nov 2025, expected to close early 2026) - the "
        "Workers-AI/Replicate surface is shifting; re-verify endpoints + pricing at integration time",
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
