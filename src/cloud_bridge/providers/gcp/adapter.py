"""Google Cloud (Vertex AI / Colab Enterprise) provider setup entry.

Setup guidance only. Researched from Google Cloud Vertex AI / Colab Enterprise
docs (2026-06); NOT yet run through this bridge.

The only AUTOMATABLE 'Colab' surface: consumer Google Colab has no run-API and
its ToS bans headless use (see docs/providers/notebook-compute.md). This entry
covers Colab Enterprise notebook executions and Vertex AI Custom Jobs. The headline
hazard is that GCP has NO hard spend cap.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter


class GcpAdapter(ProviderAdapter):
    name = "gcp"
    adapter_id = "gcp_vertex_v1"
    automated_launch = False
    category = "notebook_job"
    summary = (
        "Google Cloud is the only AUTOMATABLE 'Colab' surface (consumer Colab has no API and its ToS bans "
        "headless use - see docs). This provider entry covers Colab Enterprise notebook executions and Vertex AI Custom "
        "Jobs: gcloud/Python/Terraform, service-account auth, async submit -> GCS output. CRITICAL: GCP has "
        "NO hard spend cap - a runaway runtime bills until killed; the bridge MUST own a killswitch + set "
        "template idle-shutdown + delete runtimes."
    )
    learnings_doc = "docs/providers/notebook-compute.md"
    provenance = "researched from official docs (2026-06); not yet run through this bridge"
    roadmap = [
        "validate the surface (Colab-Enterprise notebook execution vs Vertex Custom Job), the service account "
        "+ IAM scope, and the GCS output bucket before any paid run",
        "render async submit (gcloud / Python sync=False) -> poll -> GCS output download + SHA-256",
        "HARD requirement before paid launch: a killswitch (budget -> Pub/Sub -> disable-billing) PLUS template "
        "idle-shutdown - GCP will not stop spend for you",
        "closeout: verify the execution finished AND the runtime is deleted AND GCS outputs are pulled",
    ]
    known_patterns = [
        "consumer Google Colab is NOT automatable - operationally there is no run-API and no programmatic "
        "cleanup hook; legally the paid ToS bans access 'other than by means authorized by Google' and bans "
        "reselling/sublicensing, which bind PAYING users too (the SSH/UI-bypass/distributed-worker bans are "
        "FREE-tier restrictions a paid balance lifts, so 'just buy Pro' does not legitimize headless "
        "brokering); the automatable 'Colab' is Colab Enterprise (Vertex AI), not research.google.com Colab",
        "async submit -> GCS retrieve: `gcloud colab executions create --gcs-notebook-uri ... "
        "--gcs-output-uri ...` for notebooks, or `gcloud ai custom-jobs create` for containers (gcloud can "
        "autopackage local code + push to Artifact Registry); the Python SDK supports sync=False then poll",
        "auth via a GCP service account (headless, no end-user creds) with IAM scoped to the project/region",
        "NO HARD SPEND CAP: GCP budgets are alerts, not limits, and billing data lags up to 24h - a "
        "forgotten/runaway runtime keeps billing; the bridge MUST implement its own killswitch (budget -> "
        "Pub/Sub -> Cloud Function disables billing / deletes runtime), not rely on alerts",
        "set the runtime template idle-shutdown (10-1440 min, use ~10) as a backstop so a hung runtime "
        "self-terminates",
        "cleanup: a one-time execution is job-scoped, but a CONNECTED runtime bills until stopped/deleted - "
        "delete runtimes and clean GCS output objects; track 'execution finished' and 'runtime gone' as two facts",
        "two products are named Colab, and per-region GPU quota requests are common friction points",
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
