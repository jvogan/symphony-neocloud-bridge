"""Lambda Cloud (Lambda Labs) provider setup entry.

Setup guidance only. Provider setup entry for Lambda Cloud GPU virtual machines.
Validate launch, SSH readiness, artifact egress, billing, and cleanup with a
public smoke before treating this as an launch guide.
See docs/providers/lambda.md for the detailed reference.

NOT AWS Lambda - this is Lambda Labs / Lambda Cloud GPU virtual machines.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class LambdaCloudAdapter(ProviderAdapter):
    name = "lambda"
    adapter_id = "lambda_cloud_vm_v1"
    automated_launch = False
    provenance = "setup guidance from public provider docs and sanitized bridge patterns"
    summary = (
        "Lambda Cloud GPU virtual machines (Lambda Labs; NOT AWS Lambda). HOURLY billing, "
        "no per-second, no spend cap: the instance AND any "
        "separately-billed persistent filesystem bill until EXPLICITLY terminated - closeout must "
        "verify BOTH are gone. The API sits behind Cloudflare (urllib/requests get a 403); shell out to curl."
    )
    learnings_doc = "docs/providers/lambda.md"
    roadmap = [
        "launch a GPU VM via the Lambda Cloud API - but the API is behind Cloudflare (urllib/requests "
        "get an HTML 403); shell out to curl with the key on stdin, never on argv or disk",
        "image IDs are misleading (a requested 'Stack 24.04' booted 22.04 without python3.12): pin "
        "image_id per type and hard-gate the remote script on os-release / nvidia-smi / python; re-pin torch after any source install",
        "HARD cost gate: hourly billing + explicit terminate is the only stop (no terminate_after "
        "backstop); launch/terminate must NOT auto-retry, and refuse to launch while any instance already exists",
        "closeout is N resource classes: verify instances gone AND persistent filesystems gone AND any "
        "ephemeral SSH key removed - a leftover filesystem bills independently of the instance",
        "multi-GPU (gpu_8x_*), user_data/cloud-init, and NFS create/attach APIs need separate public validation",
    ]
    known_patterns = [
        "instances are full GPU VMs, not functions; launch via the Lambda Cloud API then drive the "
        "workload over SSH (public key injected at launch)",
        "the API is behind Cloudflare - urllib/requests get an HTML 403; you MUST shell out to curl",
        "HOURLY billing, no per-second, no spend cap; a forgotten instance bills indefinitely and explicit "
        "termination is the ONLY stop (no RunPod-style terminate_after)",
        "persistent filesystems bill SEPARATELY and survive instance termination - closeout MUST confirm "
        "instances gone AND file-systems empty as two independent checks",
        "GET polls retry transient curl failures, but launch/terminate NEVER auto-retry (a double-launch "
        "burns money); refuse to launch while any instance already exists",
        "boot is async + slow (single-GPU 1-7 min observed) and billing starts only after boot+health-check; "
        "set a generous max_boot and count boot time + failed attempts in the cost tally",
        "no managed artifact egress: scp or push to S3, then verify + hash before terminate",
        "capacity varies by region and GPU type; expect launch-time unavailability and keep fallback regions/types",
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
