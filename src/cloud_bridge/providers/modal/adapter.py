"""Modal (modal.com) provider setup entry.

Setup guidance only. Provider setup entry for Modal serverless GPU/CPU functions.
Treat this guidance as design constraints until a public smoke validates launch,
cost, artifact, and cleanup behavior.
See docs/providers/modal.md for the detailed reference.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class ModalAdapter(ProviderAdapter):
    name = "modal"
    adapter_id = "modal_function_v1"
    automated_launch = False
    provenance = "setup guidance from public provider docs and sanitized bridge patterns"
    summary = (
        "Modal.com serverless GPU functions for bounded canaries / small single-container "
        "fanouts. CPU-only and multi-container fanout require public smoke validation. Default path is blocking "
        "@app.local_entrypoint -> fn.remote() on ONE container (max_containers=1, retries=0); "
        "async .spawn()/FunctionCall.get() needs separate validation. Auth via a Keychain-backed "
        "wrapper, never modal.Secret."
    )
    learnings_doc = "docs/providers/modal.md"
    roadmap = [
        "validate Modal app metadata: @app.function entrypoints, image layers, gpu=, timeout, "
        "startup_timeout, plus the cost-safety knobs max_containers=1 and retries=0",
        "render run as blocking @app.local_entrypoint() -> fn.remote(); .spawn()/FunctionCall.get() "
        "async fanout needs a separate public smoke before default use",
        "per-second billing is cheap when idle, but a wave can still cost a few $ for ZERO usable "
        "output: count failed canaries (image-build / import / runtime-gate / timeout) in the total",
        "egress is host-side: recursive `modal volume get` is UNRELIABLE; use `volume ls` + explicit "
        "per-file pulls, verify content magic + SHA-256 before trusting any returned summary",
    ]
    known_patterns = [
        "blocking @app.local_entrypoint() -> fn.remote() is the default candidate invocation "
        "(returns a summary dict); keep fanout in-process on ONE container until validated otherwise",
        "@app.function(gpu=, image=, timeout=, startup_timeout=, max_containers=1, retries=0): "
        "max_containers=1 and retries=0 bound paid concurrency; validate .spawn()/FunctionCall polling separately",
        "auth via a Keychain-backed wrapper (a macOS Keychain service of your choice); NEVER modal.Secret for the "
        "account token, never tokens in repo / .env / logs / artifacts",
        "capture the ap-* app ID from launch stdout immediately (ephemeral apps stop resolving by name "
        "after teardown); do NOT pass --name (it overrides the App display name) - identity rides args+tags",
        "billing: `modal billing report --for today --resolution h --tz local --tag-names "
        "campaign,run_id --json` (the older `billing --tag` is INVALID); hourly rows LAG -> billing_pending",
        "single-GPU containers need explicit --gpu 0 (high-index defaults like --gpu 7 fail checkpoint "
        "deserialization); batch-size 1 + torch.cuda.empty_cache() between units avoids CUDA OOM",
        "egress is host-side: recursive `modal volume get` is UNRELIABLE; use `volume ls` + per-file "
        "pulls, verify content magic (scan past the first KB) + SHA-256, never trust the returned summary",
    ]

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": self.adapter_id,
            "automated_launch": False,
            "summary": self.summary,
            "learnings_doc": self.learnings_doc,
            "portable_contract": list(PORTABLE_CONTRACT),
            "known_patterns": self.known_patterns,
            "roadmap": self.roadmap,
        }
