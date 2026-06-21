"""EvolutionaryScale ESM / Forge (biohub.ai) provider setup entry.

Setup guidance only. Researched from EvolutionaryScale ESM / Forge docs and the
ESM GitHub + LICENSE (2026-06); NOT yet run through this bridge.

The "biohub (esm)" provider entry: EvolutionaryScale's hosted ESM inference,
migrated forge.evolutionaryscale.ai -> biohub.ai. It is NOT Chan Zuckerberg
Biohub (coincidental name, different org with no hosted ESM API).
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class EsmAdapter(ProviderAdapter):
    name = "esm"
    adapter_id = "esm_forge_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "EvolutionaryScale's hosted ESM inference ('biohub (esm)') - ESM3 / ESM C / ESMFold2, "
        "migrated forge.evolutionaryscale.ai -> biohub.ai (NOT Chan Zuckerberg Biohub). Token auth, a batch "
        "executor for throughput, NOTHING to tear down. BIG TRAP: the hosted biohub.ai API is gated to "
        "research/informational use by its Terms of Use (commercial use of the hosted endpoint needs a "
        "separate agreement), and content guardrails block controlled/pathogen sequences. The ESM code + "
        "ESM C weights are MIT/commercial-OK; only ESM3-open weights are Cambrian non-commercial - the gate "
        "is the hosted ToS, not the model license."
    )
    learnings_doc = "docs/providers/bio-inference.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate model choice (ESM C 600M/6B, ESMFold2, ESM3) AND that hosted use is research/informational "
        "per the biohub.ai ToS (commercial hosted use needs a separate agreement) before any call",
        "render via the ESM SDK client + its batch executor for throughput; load the token from a secure "
        "store, never inline",
        "bound spend against the preview credits; treat a guardrail refusal as a DISTINCT failure mode from a "
        "rate-limit or an error",
        "scrub sequences through the bridge private-data policy and expect provider-side content guardrails too",
    ]
    known_patterns = [
        "managed_inference: token-metered free non-commercial preview with credits; no standing resource, so "
        "closeout is artifact retrieval + credit watch, NOT a teardown",
        "'biohub (esm)' = EvolutionaryScale Forge migrated to biohub.ai; it is NOT Chan Zuckerberg Biohub "
        "(coincidental name, different org, no hosted ESM API)",
        "auth via a Forge/biohub token from the developer console (biohub.ai/developer-console/api-keys); the "
        "SDK classes still read 'Forge' though the platform is biohub.ai (verify the exact client class names "
        "at integration)",
        "use the SDK's batch executor for concurrent high-throughput calls that respect rate limits; single "
        "calls otherwise (confirm the exact executor name)",
        "outputs: ESM C -> embeddings + logits arrays; ESMFold2 -> mmCIF - verify shape/content before trusting",
        "LICENSE/ToS TRAP: the ESM code + ESM C weights are MIT (commercial OK); only ESM3-open weights are "
        "Cambrian non-commercial. The real gate is the HOSTED biohub.ai Terms of Use, which restrict the "
        "endpoint + its Output to research/informational use - so commercial use of the hosted API needs a "
        "separate agreement regardless of the MIT model license",
        "content guardrails silently block controlled/pathogen sequences without elevated access - expect "
        "refusals and request elevated access for legitimate work",
        "the free preview is time-limited and credit-metered; exact quotas are login-gated/undocumented - "
        "verify in-app, do not assume",
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
