"""Kaggle Kernels provider setup entry.

Setup guidance only. Researched from the official kaggle-api docs (2026-06); NOT
yet run through this bridge.

The cheapest unattended GPU surface: free, quota-bound (not dollar-bound), with
an official CLI that does headless push -> status -> output.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class KaggleAdapter(ProviderAdapter):
    name = "kaggle"
    adapter_id = "kaggle_kernel_v1"
    automated_launch = False
    category = "notebook_job"
    summary = (
        "Kaggle Kernels - the cheapest UNATTENDED GPU surface: the official `kaggle` CLI does headless push "
        "-> status -> output. FREE, so there is NO bill to run away; the constraint is QUOTA not dollars "
        "(~30 GPU-h/week, ~9h/session, resets ~Sat 00:00 UTC). Token auth (KAGGLE_USERNAME/KAGGLE_KEY). GPU + "
        "internet require one-time PHONE VERIFICATION. Sessions self-terminate - cleanup is trivially satisfied."
    )
    learnings_doc = "docs/providers/notebook-compute.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate kernel-metadata.json (enable_gpu, machine_shape, enable_internet, kernel_type, is_private) "
        "before push",
        "render run as `kaggle kernels push` -> poll `kaggle kernels status` until complete/error -> `kaggle "
        "kernels output` download + SHA-256",
        "treat quota (30 GPU-h/week, 9h/session) as the budget; there is no dollar cost to cap",
        "confirm account phone-verification + kernel privacy before assigning GPU work",
    ]
    known_patterns = [
        "async submit/poll/retrieve via the official CLI: `kaggle kernels push` uploads AND triggers the run; "
        "`kaggle kernels status <user/slug>` polls; `kaggle kernels output <user/slug>` downloads artifacts + logs",
        "behavior is set in kernel-metadata.json: enable_gpu, machine_shape (NvidiaTeslaT4/P100/Tpu1VmV38), "
        "enable_internet, kernel_type (script/notebook), is_private",
        "notebook_job but FREE: no meter, so the bridge's forgotten-resource-bill risk is ~zero; the real "
        "limit is quota (~30 GPU-h/week, ~9h/session) - exhausting it throttles, it does not charge",
        "auth via an API token (kaggle.json / KAGGLE_USERNAME + KAGGLE_KEY) - headless-friendly",
        "GPU + internet need a one-time PHONE VERIFICATION on the account; you do not reliably control which "
        "GPU you are given",
        "cleanup is trivial (free, sessions self-terminate at completion or the 9h cap); for hygiene reuse a "
        "slug and keep kernels private",
        "Kaggle terms intend interactive/competition use; heavy automation is community-tolerated, not "
        "contractually guaranteed - keep usage bounded and polite",
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
