# Neocloud / Serverless-GPU Providers

Compute providers beyond RunPod/Modal/Lambda/AWS. The dominant axis for *unattended* use is
**self-terminating job (nothing to tear down) vs persistent resource (bills until torn
down)** - not price or speed. That splits this set into `managed_inference` (token/per-call
metered, cannot bill forever) and `compute_rental` (a forgotten deployment/pod bills
forever).

All facts below were **researched from official docs (2026-06) and have not yet been run
through this bridge** - verify on a first smoke before trusting any pattern as a runbook.

## Slotted providers

### fal - fal.ai `fal_queue_v1` (managed_inference)
The cleanest stateless primitive surveyed: `POST queue.fal.run/{model} -> request_id ->
poll -> GET result`; queue wait is free, nothing to tear down. Per-request controls
`X-Fal-Request-Timeout` / `X-Fal-No-Retry` (auto-retry is ON by default) /
`X-Fal-Object-Lifecycle-Preference`. **Output artifact URLs are PUBLIC by default - set a
lifecycle.** Gen-media-first.

### together - Together AI `together_v1` (managed_inference)
The only surveyed provider with a true **async Batch API** (file-submit -> poll -> download,
~50% cost, 30B-token/job ceiling). Token-metered, OpenAI-compatible. Nothing to tear down on
serverless/batch; a `dedicated` endpoint is the exception (per-minute, **no minimum**, but
bills continuously while running until explicitly stopped).

### replicate - Replicate `replicate_prediction_v1` (managed_inference)
Run any model (Cog) via async REST + webhooks. Plain predictions self-terminate (30-min
auto-timeout); **`deployments` with min_instances>=1 bill until set to 0 / deleted**, and
**output artifacts are auto-deleted after ~1 hour** (fetch promptly). Cloudflare announced
acquiring Replicate (Nov 2025, expected to close early 2026) - surface is shifting.

### beam - Beam (beam.cloud) `beam_function_v1` (compute_rental)
Modal-class arbitrary containers on a real GPU (Python decorators + CLI, per-second billing,
~2-3s cold starts). **Idle-billing trap:** `keep_warm_seconds` bills after each request
(defaults: endpoints 180s, task queues 10s, **pods 600s**); pods are VM-like (no
scale-to-zero). Teardown = `keep_warm_seconds=0` + undeploy.

## Considered, not separate provider entries

- **Fireworks / Groq / Cerebras** - ultra-fast token-metered LLM inference APIs with batch
  discounts and free tiers; **nothing to clean up.** Add one as a zero-cleanup "fast LLM"
  provider entry if/when an LLM-inference workload appears. (HuggingFace Inference Providers already
  routes to several of these behind one token - see `docs/providers/huggingface.md`.)
- **Cloudflare Workers AI** - "Neurons" billing, edge, idle-safe; increasingly relevant now
  that Cloudflare owns Replicate.
- **Vast.ai / Hyperbolic / Prime Intellect** - cheapest GPUs, but **bill-forever VM/
  marketplace rentals** with variable host reliability; interruptibles can die on ~15s
  notice. **Do NOT add as a default** - only behind a hard TTL auto-terminate, never for
  unattended work where cleanup must be guaranteed.
- **Baseten** - production serving (Truss); scale-to-zero serverless is bridge-appropriate,
  but **dedicated deployments bill continuously and have 16-90s cold starts** (wrong for
  short canaries). Only its serverless path would need a separate provider entry.

## Closeout by category

- **managed_inference (fal/together/replicate):** no resource to delete; fetch + hash the
  artifact within its retention window, bound spend by per-call/token price. The only
  teardown targets are the *persistent exceptions* - a Replicate deployment, a Together
  dedicated instance - which must be recorded and torn down.
- **compute_rental (beam):** closeout must undeploy the endpoint/queue/pod AND confirm
  `keep_warm` left no warm container billing - two checks, like a rented machine.

## Sources
replicate.com docs; fal.ai docs (queue, lifecycle headers); together.ai (Batch API);
beam.cloud docs (keep_warm_seconds); fireworks.ai / groq.com / cerebras.ai; vast.ai.
Researched 2026-06; pricing is list-rate and drifts - re-verify at integration time.
