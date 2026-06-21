"""Provider adapter contract for the Symphony cloud bridge.

The contract keeps domain workloads provider-neutral: common execution needs
live in the manifest core, provider-specific resource fields live under a named
provider block, and paid/mutating launch is allowed only when that provider has
automated launch support in this bridge.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# Every adapter must support or explicitly reject each stage, ordered roughly by
# lifecycle. Mirrors docs/provider-adapter-contract.md "Common Contract".
PORTABLE_CONTRACT: tuple[str, ...] = (
    "validate-manifest",    # validate a launch manifest without creating resources
    "contract-self-check",  # stage contract / artifact-proof / claim-boundary check
    "prepare",              # write a launch packet without creating resources
    "write-handoff",        # record the worker -> orchestrator boundary
    "validate-handoff",     # validate a handoff plus referenced manifest
    "render-startup",       # render a startup script or equivalent command
    "source-checkout",      # exact git checkout or mounted snapshot
    "poll-state",           # resource state polling
    "capture-evidence",     # heartbeat/status/log/artifact capture + SHA-256 hashes
    "egress-plan",          # durable artifact egress planning and proof
    "budget-limits",        # budget and runtime limits
    "billing-report",       # billing or estimated cost reporting
    "cleanup-closeout",     # cleanup or retention closeout
    "supervise",            # workload state -> next supervisor action
    "dashboard",            # multi-run monitoring
    "symphony-outcome",     # parseable, Linear-ready outcome
)


class ProviderLaunchUnsupported(NotImplementedError):
    """A provider is known, but this bridge cannot launch it directly."""

    def __init__(self, provider: str, *, reason: str = "", roadmap: list[str] | None = None) -> None:
        self.provider = provider
        self.reason = reason or (
            f"the {provider!r} provider has setup guidance but no automated launch "
            "support in this bridge"
        )
        self.roadmap = roadmap or []
        super().__init__(self.reason)


class ProviderAdapter(ABC):
    """Base class for a compute-provider execution adapter.

    The adapter layer is the registry, capability, contract, and safety gate:
    it lets the bridge expose provider guidance while refusing to fake a paid
    launch on a provider path it cannot drive directly.
    """

    name: str = ""
    adapter_id: str = ""
    automated_launch: bool = False
    # How the provider is consumed, which fixes its cleanup/budget semantics:
    #   compute_rental    - rent a machine/function; a forgotten resource bills forever (RunPod, Modal, Lambda)
    #   managed_inference - call a hosted model API; usually nothing to tear down, watch token/credit budget
    #   notebook_job      - submit a notebook/job to managed hardware; delete runtimes/kernels to stop billing
    category: str = "compute_rental"
    # Where this provider guidance comes from, so a human/agent can distinguish
    # automated launch support from setup-only notes.
    provenance: str = ""
    summary: str = ""
    learnings_doc: str = ""
    roadmap: list[str] = []

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return the capability/contract descriptor for this provider."""

    def launch_surface(self) -> dict[str, Any]:
        """How this provider can be launched through this bridge."""
        return {
            "automated_launch": self.automated_launch,
            "cli_commands": [],
            "note": "setup guidance only; automated launch is blocked" if not self.automated_launch else "",
        }

    def assert_launch_supported(self) -> None:
        """Gate paid/mutating work: raise unless automated launch is supported."""
        if not self.automated_launch:
            raise ProviderLaunchUnsupported(self.name, reason=self.summary, roadmap=self.roadmap)

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": self.adapter_id,
            "automated_launch": self.automated_launch,
            "category": self.category,
            "provenance": self.provenance,
            "summary": self.summary,
            "learnings_doc": self.learnings_doc,
            "roadmap": list(self.roadmap),
            "portable_contract": list(PORTABLE_CONTRACT),
        }
