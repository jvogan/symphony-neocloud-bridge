"""NVIDIA BioNeMo / NIM (build.nvidia.com) provider setup entry.

Setup guidance only. Researched from NVIDIA build.nvidia.com / NIM docs (2026-06);
NOT yet run through this bridge.

Widest single-vendor bio menu (AlphaFold2, ESMFold, RFdiffusion, ProteinMPNN,
DiffDock, Boltz-2) behind one nvapi- key. Two tiers with OPPOSITE cleanup
semantics - the hosted tier is bounded; the self-host/SageMaker tier bills until
torn down.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class NvidiaNimAdapter(ProviderAdapter):
    name = "nvidia-nim"
    adapter_id = "nvidia_nim_v1"
    automated_launch = False
    category = "managed_inference"
    summary = (
        "NVIDIA BioNeMo / NIM hosted inference (build.nvidia.com): the widest single-vendor bio menu "
        "(AlphaFold2, ESMFold, RFdiffusion, ProteinMPNN, DiffDock, Boltz-2) behind one nvapi- key. The "
        "HOSTED tier is bounded (1,000 free credits, 40 req/min) with nothing to tear down; the SELF-HOSTED "
        "NIM / SageMaker tier is the opposite - it bills continuously and MUST be torn down. Long jobs are "
        "async: HTTP 202 -> poll a status endpoint."
    )
    learnings_doc = "docs/providers/bio-inference.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate which NIM + its per-model JSON schema (not uniform) and confirm hosted vs self-host tier "
        "before any call",
        "render the hosted run as POST -> HTTP 202 -> poll the NVCF status endpoint; wire nvapi- auth from a "
        "secure store",
        "if (and only if) a self-hosted NIM / SageMaker deploy is required, attach the compute_rental teardown "
        "contract: record the endpoint id and delete it at closeout",
        "bound spend against the 1,000 free credits + 40 rpm; treat credit exhaustion and rate-limit as "
        "distinct failures",
    ]
    known_patterns = [
        "TWO TIERS, opposite cleanup: hosted build.nvidia.com (nvapi- key, no infra) = managed_inference, "
        "nothing to tear down; self-hosted NIM container / AWS SageMaker (Boltz2-NIM is on AWS Marketplace) + "
        "NVIDIA AI Enterprise (~$4,500/GPU/yr) = compute_rental that bills until deleted",
        "async for biology/long jobs: POST returns HTTP 202 Accepted -> GET the NVCF status endpoint to poll "
        "for the result; small models may return inline",
        "auth via an nvapi- key from the free NVIDIA Developer Program (no card/GPU for the hosted tier)",
        "hosted limits: 1,000 free credits (5,000 on request), 40 req/min - batch throttles or exhausts "
        "credits quietly; bound fanout",
        "per-model JSON schemas vary (FASTA in, PDB/structure out for AF2/ESMFold; poses/embeddings elsewhere) "
        "- there is NO uniform contract across NIMs",
        "do not wander from the free hosted endpoint into a paid SageMaker / self-host NIM deploy without the "
        "compute_rental teardown contract - that is where the bridge's cleanup guarantee actually bites",
        "the hosted model catalog shifts over time; pin the model + version you validated and confirm a "
        "specific model (e.g. ESMFold) is in the current build.nvidia.com catalog before relying on it",
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
