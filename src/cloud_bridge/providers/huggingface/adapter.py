"""HuggingFace provider entry.

HF spans three drivable surfaces with very different cleanup: HF Jobs (ephemeral,
auto-terminating - the best fit, and the surface this adapter now drives), Inference
Endpoints (dedicated, bills 24/7 - the forgotten-bill trap, still setup guidance), and
Inference Providers (stateless serverless, nothing to tear down, still setup guidance).
Naming churn: the old "Inference API" is now "Inference Providers"; the CLI `huggingface-cli`
is now `hf`.

The Jobs surface is driven through the bridge's `run-job` command over the documented REST API
(submit -> poll -> verify pushed artifacts -> cancel-on-abort); see providers/huggingface/jobs.py
and docs/providers/huggingface.md. Endpoints and Inference Providers remain setup-guidance paths.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class HuggingfaceAdapter(ProviderAdapter):
    name = "huggingface"
    adapter_id = "huggingface_v1"
    automated_launch = True
    # Jobs is a one-shot job on managed hardware that auto-terminates:
    # notebook_job semantics, not the bills-forever compute_rental of Endpoints.
    category = "notebook_job"
    summary = (
        "HuggingFace, three drivable surfaces: HF JOBS (ephemeral 'docker run on HF GPUs', per-minute, "
        "default 30-min timeout auto-stop - the best bridge fit, with automated launch via run-job; "
        "needs a positive credit balance / payment method, no free-tier canaries), INFERENCE ENDPOINTS "
        "(dedicated deployment that bills 24/7 until scale_to_zero/pause/DELETE - the forgotten-bill trap, "
        "still setup guidance), and INFERENCE PROVIDERS (stateless serverless router, nothing to tear down, has a "
        "free tier, still setup guidance). Jobs is driven through this bridge's REST client (stdlib urllib); the "
        "other surfaces would use the huggingface_hub client / `hf` CLI. Auth via an hf_ token."
    )
    learnings_doc = "docs/providers/huggingface.md"
    provenance = (
        "HF Jobs surface has automated launch support over the documented REST API through this bridge's "
        "run-job command; first live smoke PASSED 2026-06 (job completed, artifacts verified, closeout "
        "succeeded). Inference Endpoints and Inference Providers remain researched (2026-06), not yet "
        "run through this bridge."
    )
    roadmap = [
        "HF Jobs: DONE - submit (image/command/flavor/timeout/secrets/volumes) -> poll status.stage -> "
        "fetch logs -> verify artifacts pushed to a Hub repo -> cancel-on-abort, via the run-job command "
        "(providers/huggingface/{rest,jobs}.py). Next: read-write bucket-volume egress once the bucket "
        "read-back URL is confirmed at first smoke",
        "Inference Endpoints next (the cleanup-critical surface): create -> wait(timeout) until running -> "
        "invoke -> closeout via delete() for zero residual (pause() also stops billing with manual resume; "
        "scale_to_zero auto-wakes on a stray request)",
        "Inference Providers as a cheap zero-cleanup smoke path: stateless per-call, capped by credit balance, "
        "pin provider= for reproducibility",
        "naming: 'Inference API' -> 'Inference Providers'; the `huggingface-cli` was REMOVED in huggingface_hub "
        "v1.0 (use `hf`); Jobs needs a positive credit balance",
    ]
    known_patterns = [
        "THREE surfaces, three cleanup postures: HF Jobs = ephemeral + auto-terminates (default 30-min "
        "timeout, raise it or training dies silently); Inference Endpoints = dedicated, bills 24/7 until torn "
        "down; Inference Providers = stateless, nothing to tear down",
        "HF Jobs is the best structural fit: run_job(image=, command=[...], flavor=, timeout=, secrets=, "
        "volumes=) -> JobInfo.id; wait_for_job (accepts a LIST, never raises - check status.stage), "
        "cancel_job, fetch_job_logs; CLI `hf jobs run/ps/logs/inspect/cancel/wait`. Billed per-minute only "
        "while Running, NOT during image build",
        "HF Jobs artifacts: logs only by default - mount a Volume (type model/dataset/bucket) or push to the "
        "Hub, the container's filesystem vanishes on exit; JOB_ID env is auto-injected",
        "Inference Endpoints is the forgotten-bill trap: a running min_replica>=1 endpoint bills continuously "
        "regardless of traffic; the safe zero-residual terminal action is delete(); pause() also stops billing "
        "(manual resume, keeps config) and scale_to_zero() stops billing but AUTO-WAKES on a stray request. "
        "delete() also nukes logs/metrics - capture them first. wait(timeout=) is mandatory (provisioning takes minutes)",
        "Inference Providers (formerly 'Inference API'): one hf_ token routes to 15+ providers via "
        "router.huggingface.co (provider='auto' or pinned); OpenAI-compatible chat path; per-call billing "
        "capped by credit balance (Free ~$0.10, PRO $2/mo); zero cleanup",
        "auth via an hf_ token; for agents use a FINE-GRAINED token (Jobs/Endpoints need write on the "
        "namespace; Inference Providers needs the 'Make calls to Inference Providers' permission); secrets= is "
        "encrypted server-side",
        "HF Jobs needs a positive credit balance / payment method (no free-tier Jobs canaries) and ZeroGPU "
        "hosting needs Pro/Team/Enterprise; Inference Providers has a free tier and is the cheapest no-cost "
        "smoke; Spaces/ZeroGPU is a Gradio app surface, a poor fit for unattended batch",
    ]

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": self.adapter_id,
            "automated_launch": True,
            "category": self.category,
            "provenance": self.provenance,
            "summary": self.summary,
            "learnings_doc": self.learnings_doc,
            "portable_contract": list(PORTABLE_CONTRACT),
            "known_patterns": self.known_patterns,
            "roadmap": self.roadmap,
            # automated_launch=True is surface-scoped: only Jobs has a guarded launch path.
            "surfaces": {
                "jobs": "automated_launch",
                "inference_endpoints": "setup_guidance",
                "inference_providers": "setup_guidance",
            },
        }

    def launch_surface(self) -> dict[str, Any]:
        return {
            "automated_launch": True,
            "cli_commands": ["run-job"],
            "note": (
                "Only the HF Jobs surface has automated launch support; it is driven through "
                "run-job (submit/poll/verify/cancel). Inference Endpoints and Inference "
                "Providers remain setup guidance."
            ),
        }

    def client(self, **kwargs: Any) -> Any:
        """Construct a HF Jobs REST client. Imported lazily to keep import cheap."""
        from cloud_bridge.providers.huggingface.rest import HfJobsClient

        return HfJobsClient(**kwargs)
