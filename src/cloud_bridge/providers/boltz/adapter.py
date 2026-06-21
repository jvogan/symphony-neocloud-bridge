"""Boltz API (boltz.bio) provider setup entry.

Setup guidance only. Researched from the official boltz.bio / api.boltz.bio docs
(2026-06); NOT yet run through this bridge - treat known_patterns as documented
behavior to verify on a first smoke, not a validated runbook.

First-party hosted co-folding API from Boltz PBC (Boltz-2 structure + binding
affinity, BoltzMol/BoltzProt design). NAMESAKE TRAP: unrelated to boltz.exchange,
a Bitcoin Lightning swap service - discard any boltz.exchange results.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class BoltzAdapter(ProviderAdapter):
    name = "boltz"
    adapter_id = "boltz_api_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "Boltz.bio first-party hosted co-folding API (Boltz-2 structure + binding affinity, "
        "BoltzMol/BoltzProt design). Per-prediction billing (~$0.025, 200 free/month) with NOTHING "
        "to tear down - stop calling = stop paying. Async job model: run() (blocking) or start() -> poll -> "
        "retrieve() (mmCIF + metrics.json + PAE). Auth via the x-api-key header. Brand-new (June 2026) - pin the model id."
    )
    learnings_doc = "docs/providers/bio-inference.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate Boltz job metadata: model id (e.g. boltz-2.1), input schema (sequence/ligand), and "
        "which capability (predict / affinity / design) before any paid call",
        "render run as run() (blocking) or start() -> poll the job id -> retrieve() (CLI: download-results "
        "polls/resumes); wire the async download + artifact SHA-256, never assume a synchronous return",
        "bound spend: count predictions against the 200/month free tier + per-prediction price; there is "
        "no resource to leak but a fanout can silently burn credits against unpublished rate limits",
        "scrub inputs: unpublished sequences must pass the bridge private-data policy before any external call",
    ]
    known_patterns = [
        "managed_inference: per-prediction billing (~$0.025/pred, 200 free/month); no standing resource, "
        "so closeout is artifact retrieval + spend tally, NOT a teardown",
        "async job model: high-level run() blocks/polls, or start() -> retrieve(); the CLI `download-results` "
        "polls/resumes a job. Handle the job-id lifecycle, do not assume a sync result",
        "auth via an API key in the x-api-key header (console signup at boltz.bio); first-party Claude "
        "Code / Codex / Gemini CLI integrations exist",
        "outputs: *_predicted_structure.cif (mmCIF), metrics.json, PAE .npz, archive.tar.gz; SDK downloads "
        "into boltz-experiments/ - verify the CIF content + SHA-256 before trusting any summary",
        "pin the model string (e.g. boltz-2.1): API/SDK are weeks old (June 2026) and will drift",
        "per-account rate/throughput limits are unpublished; bulk screening can hit silent caps or burn "
        "credits - bound fanout and watch the credit balance",
        "NAMESAKE TRAP: boltz.exchange is an unrelated Bitcoin Lightning service (HMAC auth) - every bridge "
        "fact must come from boltz.bio / api.boltz.bio only",
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
