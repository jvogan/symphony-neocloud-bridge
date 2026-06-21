"""Together AI provider setup entry.

Setup guidance only. Researched from together.ai docs (2026-06); NOT yet run
through this bridge.

Hosted open-model inference with the survey's only true async Batch API. Token-
metered, so a forgotten serverless/batch job cannot bill forever; dedicated
instances are the one persistent exception.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class TogetherAdapter(ProviderAdapter):
    name = "together"
    adapter_id = "together_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "Together AI hosted open-model inference (no box to manage) with the survey's only true ASYNC BATCH "
        "API: file-submit -> poll -> download at ~50% cost (30B-token/job ceiling). Token-metered "
        "(per-token), so a forgotten job CANNOT bill forever - nothing to tear down on serverless/batch. "
        "OpenAI-compatible REST. Exception: DEDICATED endpoints bill per-minute with NO minimum, but bill "
        "continuously while running until explicitly stopped."
    )
    learnings_doc = "docs/providers/neoclouds.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate model id + whether the workload is real-time (serverless) or non-interactive (batch) before "
        "any paid call",
        "render batch as file-submit -> poll -> download (~50% cost); render real-time as an OpenAI-compatible "
        "request; wire artifact fetch + SHA-256",
        "if a dedicated endpoint is used, attach the compute_rental teardown contract: record it and stop it at "
        "closeout (bills continuously while running, no minimum)",
        "bound spend per-token; prefer batch for bounded canaries where latency does not matter",
    ]
    known_patterns = [
        "OpenAI-compatible REST + a real async Batch API (file-submit -> poll -> download) at ~50% cost with a "
        "30B-token/job ceiling - the right surface for bounded LLM/bio-text fanout",
        "token-metered (per-token, ~$0.27-3.00/M); serverless and batch have nothing to tear down and CANNOT "
        "bill forever - managed_inference",
        "auth via a Together API key; ~$25 signup credit, no serverless minimum spend",
        "EXCEPTION: a `dedicated` endpoint is compute_rental - per-minute with NO minimum, but bills "
        "continuously while running regardless of traffic; record and stop/delete it at closeout, do not leave it running",
        "batch trades latency for cost - use it for non-interactive canaries, not when a result is needed in-loop",
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
