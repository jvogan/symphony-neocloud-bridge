# Bio-Inference Providers (managed_inference)

Hosted biomolecular / protein inference APIs. These are `managed_inference`: you call
a hosted model, billing is per-call/per-credit, and there is **nothing to tear down** -
closeout is artifact retrieval + a spend/credit tally, not a resource teardown. The one
exception is self-hosted NIM / SageMaker deployments (see NVIDIA below), which DO bill
continuously and must be torn down like a rented machine.

All facts below were **researched from official docs (2026-06) and have not yet been run
through this bridge** - verify on a first smoke before trusting any pattern as a runbook.
Several of these APIs are weeks-to-months old and fast-moving.

## Slotted providers

### boltz - Boltz API (boltz.bio) `boltz_api_v1`
First-party hosted co-folding (Boltz-2 structure + binding affinity, BoltzMol/BoltzProt
design). Async `run()` (blocking) or `start() -> poll -> retrieve()` (CLI: `download-results`);
per-prediction billing (~$0.025,
200 free/month); `x-api-key` auth; outputs mmCIF + metrics.json + PAE. Pin the model id
(e.g. `boltz-2.1`) - the API/SDK are new (June 2026). **Namesake trap:** boltz.exchange
is an unrelated Bitcoin Lightning service - ignore it entirely. See the provider entry's
`known_patterns` for detail.

### esm - EvolutionaryScale ESM / Forge (biohub.ai) `esm_forge_v1`
The "biohub (esm)" provider entry: ESM3 / ESM C / ESMFold2, migrated forge.evolutionaryscale.ai
-> **biohub.ai** (NOT Chan Zuckerberg Biohub - coincidental name). Token auth, a batch
executor for throughput, embeddings/logits or mmCIF out. **License/ToS trap:** the ESM code +
ESM C weights are now **MIT (commercial OK)**; only ESM3-open weights stay Cambrian
non-commercial. The real gate is the **hosted biohub.ai Terms of Use**, which restrict the
endpoint + its Output to research/informational use - so commercial use of the *hosted API*
needs a separate agreement regardless of the MIT model license. Content guardrails also block
controlled/pathogen sequences. Research canaries yes; hosted product use needs that agreement.

### nvidia-nim - NVIDIA BioNeMo / NIM (build.nvidia.com) `nvidia_nim_v1`
Widest single-vendor bio menu (AlphaFold2, ESMFold, RFdiffusion, ProteinMPNN, DiffDock,
Boltz-2) behind one `nvapi-` key. Async `POST -> HTTP 202 -> poll status`. **Two tiers,
opposite cleanup:** the hosted tier is bounded (1,000 free credits, 40 req/min, nothing to
tear down); the self-hosted NIM / AWS SageMaker tier is `compute_rental` that bills until
deleted. This is the bio provider where the bridge's cleanup guarantee actually bites.

## Considered, not separate provider entries

- **BioLM.ai (aggregator)** - one uniform REST API (`/api/v3/{model}/{action}/`, `Token`
  auth) over ~70 models incl. ESMFold, Boltz-2, Chai-1, AlphaFold2, ProteinMPNN. Sync,
  per-call, no cleanup. **The lowest-integration-cost surface** if first-party SDKs are too
  much; a strong "one provider entry, many models" fallback worth promoting later. Tamarind
  Bio and Neurosnap are similar hosted wrappers.
- **AlphaFold Server (alphafoldserver.com)** - **NOT bridge-adaptable: no run-API (web UI
  only), ~30 jobs/day cap, non-commercial.** For automatable AF3-class structure use NVIDIA
  NIM, Boltz, Chai-1, or an aggregator. (The AlphaFold *Database* API at alphafold.com/EBI
  only retrieves precomputed structures - fine for lookups, useless for running new jobs.)
- **Chai Discovery** - Chai-1 (AF3-class) is reachable mainly via aggregators (first-party
  API under-documented); **Chai-2 (binder design) has no public API** (early-access partners
  only). Reach Chai-1 through BioLM/Tamarind rather than a dedicated provider entry for now.

## Closeout for managed_inference

No pod/instance to delete. Closeout = (1) fetch + SHA-256 the returned artifact within any
retention window, (2) validate the artifact content (not just HTTP 200), (3) tally
predictions/credits against the free tier and any per-account rate limit, (4) treat a
content-guardrail refusal as a distinct failure mode from a rate-limit or error.

## Sources
boltz.bio / api.boltz.bio docs; github.com/evolutionaryscale/esm (+ LICENSE) and the
EvolutionaryScale/biohub.ai developer console; build.nvidia.com / NVIDIA NIM docs;
alphafoldserver.com ToS; biolm.ai. Researched 2026-06; re-verify SDK surface, pricing, and
rate limits at integration time.
