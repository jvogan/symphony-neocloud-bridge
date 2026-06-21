# HuggingFace (huggingface_v1)

HuggingFace exposes three programmatically-drivable compute surfaces with **very different
cleanup postures**. The **HF Jobs** surface has **automated launch support in this bridge** — driven by the
`run-job` command; its `category` is `notebook_job` (a one-shot job on managed hardware that
auto-terminates). Inference Endpoints (bill 24/7 — the forgotten-bill trap) and Inference
Providers (stateless) remain **setup guidance**.

**Naming churn (HF renames often):** the old "Inference API" / "Serverless Inference API" is
now **Inference Providers**; the CLI `huggingface-cli` was **removed in `huggingface_hub` v1.0**
(use **`hf`**). "Inference Endpoints" (dedicated) is a stable, separate name. **HF Jobs** is the
newest surface.

The HF Jobs facts below are **mapped to the documented REST API; the first live smoke is
pending a paid HF account** (see "Bridge support here"). The Inference Endpoints / Inference
Providers facts were **researched from official docs (2026-06) and have not yet been run through
this bridge** - verify on a first smoke.

## Build order

### 1. HF Jobs - IMPLEMENTED (best structural fit)
"`docker run`, but on HF GPUs": submit an image + command + flavor, it runs to completion and
**auto-terminates**. Its lifecycle *is* the bridge's lifecycle.
- `run_job(image=, command=[...], flavor=, timeout=, env=, secrets=, volumes=[...])` ->
  `JobInfo.id`; `wait_for_job([...])` (accepts a list, never raises - check `status.stage`);
  `inspect_job`, `fetch_job_logs`, `fetch_job_metrics`, `cancel_job`. CLI mirrors all:
  `hf jobs run/ps/logs/inspect/cancel/wait/hardware`.
- **Per-minute billing, only while Starting/Running** (not during image build). Flavors from
  `cpu-basic` $0.01/hr to `a100-large` $2.50, `h200` $5.00, `h200x8` $40.00; list via
  `hf jobs hardware`.
- **Built-in cost guardrail: default `timeout` = 30 min, then auto-stop** - a forgotten job
  self-terminates. Raise `timeout` for long training or it dies silently at 30 min.
- **Artifacts:** logs only by default - **mount a `Volume`** (type model/dataset/bucket) or
  push to the Hub; the container filesystem vanishes on exit. `JOB_ID` is auto-injected.
- **Gated by a positive credit balance / payment method** (NOT a subscription tier) - no
  free-tier Jobs canaries; any user/org with credits can run them.

#### Bridge support here (`run-job`)
The bridge drives Jobs over the raw REST API with a stdlib `urllib` client (no `huggingface_hub`
at runtime — see `providers/huggingface/{rest,jobs}.py`), so the lifecycle is explicit and audited:
- **Submit** `POST https://huggingface.co/api/jobs/{namespace}` — body built from the manifest's
  `huggingface` block (`command` / `dockerImage`|`spaceId` / `flavor` / `timeoutSeconds` /
  `environment` / `secrets` / `volumes`). `timeoutSeconds` is always sent (HF's 30-min default
  would otherwise kill long jobs silently).
- **Poll** `GET .../{namespace}/{job_id}` until `status.stage` is terminal
  (`COMPLETED`/`CANCELED`/`ERROR`/`DELETED`); anything else (including the undocumented
  `UPDATING`) means keep polling. Exceeding the local `--poll-timeout-seconds` **cancels** the
  job (`POST .../cancel`) to stop billing — the batch analog of pod cleanup.
- **Logs** `GET .../{job_id}/logs?tail=N` (SSE `data: {…}` lines) are captured best-effort as the
  evidence log file.
- **Egress** = `artifact_egress.mode: "hf_hub_repo"`. The job persists its own outputs before
  exit — it **self-pushes** each expected artifact to a Hub repo (`repo_id` / `repo_type` /
  `revision`) using its `HF_TOKEN` (passed as an encrypted **secret**, never plaintext
  `environment`). After `COMPLETED`, the bridge downloads each from the repo's `resolve/` URL,
  hashes it, and writes `egress_status.json` — the same evidence `closeout` consumes. A
  read-write **bucket** volume is the cleaner egress, but its read-back URL is **to be confirmed
  at first smoke**, so v1 uses the documented dataset/model `resolve/` path.
- **Spend guard:** `--max-spend-usd` blocks submission when the worst-case (flavor $/min ×
  `timeoutSeconds`) exceeds it; the estimate is refined from the live `/api/jobs/hardware`
  catalog at execute time. An unpriced flavor blocks (cannot prove it is under budget).
- **Secrets:** `huggingface.secret_refs` maps a job-secret name to a **local env-var name**
  (e.g. `{"HF_TOKEN": "HF_TOKEN"}`), resolved from the environment at execute and sent in the
  encrypted `secrets` field; the audit record shows secret **names only**.

**Live smoke (the one credentialed step):** Jobs are **pay-as-you-go** (per-minute, billed to a
card) — **no Pro/Team/Enterprise subscription required.** You only need a **positive credit
balance** (add a credit card at huggingface.co/settings/billing; HF uses prepaid credits with
optional auto-recharge, billed separately from any subscription — verified against the current
pricing + billing docs, 2026-06) and `HF_TOKEN` in the environment (populated machine-side by a
Keychain wrapper; never stored in-repo). A `cpu-basic` smoke is a fraction of a cent ($0.01/hr).
Point `huggingface.namespace` and `artifact_egress.repo_id` at your own, then:
```
cloud-bridge run-job examples/hf-job/launch_manifest.json \
  --execute --yes-run-paid-hf-job --max-spend-usd 0.05
```
A clean run ends `status: artifacts_verified`; follow with `cloud-bridge closeout` over the same
out-dir for the final succeeded/failed gate.

**First smoke (2026-06): PASSED.** A `cpu-basic` job completed in ~11s (5s scheduling + 6s running,
≈ $0.00002) → `artifacts_verified` → `closeout` `succeeded`. Confirmed working: the live
`/api/jobs/hardware` cost refinement (`cost_estimate.basis: live_catalog`) and the dataset
`resolve/` egress download (artifact pulled back + hashed). The read-write **bucket** read-back
remains the one unexercised egress path.

### 2. Inference Endpoints - second (the cleanup-critical surface)
Dedicated, autoscaling, HF-managed deployment of one model. **This is the forgotten-bill
trap the bridge exists to defend against:** a running `min_replica>=1` endpoint **bills
continuously regardless of traffic**.
- `create_inference_endpoint(...)` (or `..._from_catalog`) -> `endpoint.wait(timeout=)` until
  `status="running"` -> `endpoint.client` to invoke. Provisioning takes minutes, so
  `wait(timeout=)` is mandatory for unattended runs.
- **Three idle states, three billings:** `scale_to_zero()` (not billed, auto-wakes on next
  request, cold start), `pause()` (not billed, manual `resume()`), and running-with-min>=1
  (**bills 24/7**). The **safe zero-residual terminal action is `delete()`**. `pause()` also
  stops billing (manual `resume()`, keeps config); `scale_to_zero()` stops billing but
  **auto-wakes** on a stray request, so it is not a safe terminal state for an unattended run.
  `delete()` also nukes logs/metrics, so capture them first.

### 3. Inference Providers - cheap zero-cleanup smoke path
Stateless serverless router: one `hf_` token -> 15+ third-party providers (Cerebras, Groq,
Together, Fireworks, Replicate, fal, …) via `router.huggingface.co`. OpenAI-compatible chat
path or the `InferenceClient` for all task types. **Per-call billing capped by credit
balance** (Free ~$0.10, PRO $2/mo), **nothing to tear down.** Pin `provider=` for
reproducible canaries (`provider="auto"` can change the serving provider under you). The
cheapest no-cost smoke before spending on Jobs.

### Not for unattended batch: Spaces + ZeroGPU
A Gradio app surface (GPU borrowed per `@spaces.GPU` call, auto-released). Drivable via
`gradio_client` but it is a demo/serving surface, not a submit-a-batch API. Gradio-only;
Pro/Enterprise to host ZeroGPU. Poor fit for the bridge - skip.

## Auth
An `hf_` token; for agents use a **fine-grained** token — Jobs/Endpoints need **write** on the
namespace, and Inference Providers needs the **"Make calls to Inference Providers"** permission.
`secrets=` on Jobs is encrypted server-side. Never inline the token (bridge secret policy).

## Closeout
- **HF Jobs:** bounded by `timeout`; confirm `status.stage` COMPLETED, pull logs +
  volume/Hub artifacts + hash. Only lingering-cost vector is a live **scheduled** job -
  `suspend_scheduled_job` / `delete_scheduled_job`.
- **Inference Endpoints:** the load-bearing cleanup - `delete()` for a guaranteed zero
  residual; capture logs/metrics first.
- **Inference Providers:** none; bound by credit balance.

## Sources
huggingface.co/docs/huggingface_hub (jobs, inference_endpoints), huggingface.co/docs/hub
(jobs-pricing, jobs-manage), huggingface.co/docs/inference-providers, spaces-zerogpu.
Researched 2026-06 (huggingface_hub v1.20.1); HF renames surfaces often - re-verify names at
integration time.
