"""Beam (beam.cloud) serverless-GPU provider setup entry.

Setup guidance only. Researched from beam.cloud docs (2026-06); NOT yet run
through this bridge.

A Modal-class platform for arbitrary containers on a real GPU. compute_rental:
the trap is keep_warm_seconds idle billing, and pods are VM-like (no scale-to-zero).
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class BeamAdapter(ProviderAdapter):
    name = "beam"
    adapter_id = "beam_function_v1"
    automated_launch = False
    category = "compute_rental"
    summary = (
        "Beam.cloud serverless GPU functions / sandboxes / jobs - a Modal-class platform for running "
        "ARBITRARY containers on a real GPU (Python SDK + CLI, per-second billing, ~2-3s cold starts). "
        "compute_rental, and the trap is idle billing: keep_warm_seconds bills AFTER each request (defaults: "
        "endpoints 180s, task queues 10s, PODS 600s) and pods are VM-like (no scale-to-zero). Teardown = "
        "keep_warm_seconds=0 + undeploy endpoints/queues/pods."
    )
    learnings_doc = "docs/providers/neoclouds.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate Beam app metadata: gpu=, image, and the cost-safety knob keep_warm_seconds=0 before any "
        "paid deploy",
        "render run as a decorated function/endpoint with keep_warm_seconds=0; use pods only with an explicit "
        "terminate contract",
        "closeout MUST undeploy the endpoint/queue/pod AND verify no warm container remains - a leftover "
        "keep_warm bills",
        "bound spend with per-second billing + a max runtime; count cold start + idle in the tally",
    ]
    known_patterns = [
        "compute_rental: Python decorators (@function/@endpoint) + CLI; per-second billing only while a container "
        "runs, no cold-start charge, fastest cold starts (~2-3s)",
        "IDLE-BILLING TRAP: keep_warm_seconds bills idle time after each request - defaults endpoints 180s, "
        "task queues 10s, PODS 600s; set keep_warm_seconds=0 for bounded canaries",
        "pods are on-demand VM-like resources that do NOT scale to zero - the most dangerous surface; treat a "
        "Beam pod like a rented VM and terminate it explicitly",
        "closeout MUST undeploy endpoints/queues/pods AND confirm keep_warm did not leave a warm container "
        "billing - two checks, like a rented machine",
        "open-source beta9 runtime underneath; smaller ecosystem than Modal; volume idle-cost is undocumented "
        "- verify before relying on it",
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
