# Notebook-Style Compute (notebook_job)

Submit a notebook/script to managed hardware, retrieve outputs, stop billing. This is where
"can the bridge use Google Colab?" gets answered honestly: **consumer Colab cannot be
automated; Kaggle and Google Cloud (Vertex / Colab Enterprise) are the real automatable
notebook surfaces.**

All facts below were **researched from official docs (2026-06) and have not yet been run
through this bridge** - verify on a first smoke before trusting any pattern as a runbook.

## Slotted providers

### kaggle - Kaggle Kernels `kaggle_kernel_v1`
The cheapest unattended GPU surface. Official CLI: `kaggle kernels push` (uploads AND
triggers the run) -> `kaggle kernels status <user/slug>` (poll) -> `kaggle kernels output`
(download artifacts + logs). Behavior set in `kernel-metadata.json` (`enable_gpu`,
`machine_shape`, `enable_internet`, `is_private`). **FREE - so there is no bill to run
away;** the limit is **quota** (~30 GPU-h/week, ~9h/session, resets ~Sat 00:00 UTC). GPU +
internet need a one-time **phone verification**. Sessions self-terminate, so the cleanup
guarantee is trivially satisfied. Ideal for small bounded GPU bursts.

### gcp - Google Cloud (Vertex AI / Colab Enterprise) `gcp_vertex_v1`
The automatable "Colab": `gcloud colab executions create` (notebook -> GCS output) or
`gcloud ai custom-jobs create` (container; gcloud can autopackage local code). Service-
account auth, async submit -> GCS retrieve. **CRITICAL: GCP has NO hard spend cap** -
budgets are alerts, not limits, and billing lags up to 24h, so a runaway runtime keeps
billing. Before any paid launch the bridge MUST own a killswitch (budget -> Pub/Sub ->
Cloud Function disables billing / deletes the runtime) AND set the runtime-template
idle-shutdown (10-1440 min, use ~10). Closeout = execution finished AND runtime deleted AND
GCS outputs pulled.

## Do NOT build: consumer Google Colab

Consumer Colab (Free / Pro / Pro+ / Pay-As-You-Go) is **not automatable for an unattended
agent**, and a future agent should not spend effort trying:

- **Operationally disqualifying (the load-bearing reason): no official API** to start a
  runtime, run code headlessly, and fetch outputs, and **no programmatic cleanup hook** -
  Colab is a browser front-end to an ephemeral VM. Google: it is "not possible to run Colab
  notebooks programmatically ... unless you leave your browser running." If the driver dies
  mid-run there is no API to guarantee teardown. This alone fails the bridge guarantees
  (bounded, audited, guaranteed cleanup).
- **Legally: upgrading does not legitimize it.** The paid ToS bans accessing the service
  "other than by means authorized by Google" and bans reselling/sublicensing - and those
  clauses bind **paying** users, so "just buy Colab Pro" does not make headless brokering
  compliant. (Note: the SSH / UI-bypass / distributed-worker bans people often cite are
  **free-tier** restrictions that a positive paid-compute balance *lifts* - so they are not
  the reason; the "authorized means" + anti-reselling clauses are.)
- The only "automation" is headless-Selenium UI-driving (e.g. `colabctl`): it breaks on any
  UI change and faces idle-disconnect + non-guaranteed dynamic GPU allocation, on top of the
  operational and legal disqualifiers above.

**Use Kaggle for cheap bounded bursts and Vertex / Colab Enterprise for serious jobs -
never consumer Colab.**

## Closeout for notebook_job

- **kaggle:** nothing bills (free); confirm the run reached `complete`/`error`, pull output +
  hash, and respect the weekly/session quota as the budget. For hygiene reuse a slug and keep
  kernels private.
- **gcp:** the hazard is the no-hard-cap billing model. Verify the execution finished AND the
  connected runtime is deleted (a connected runtime bills even after the execution ends), and
  rely on the killswitch + idle-shutdown, not GCP budget alerts.

## Sources
Colab FAQ + Paid ToS v4 (research.google.com/colaboratory); Colab Enterprise schedule/runtime
docs + Vertex AI custom-job docs (cloud.google.com); Kaggle kernel-metadata + commands
(github.com/Kaggle/kaggle-api); GCP budgets-are-not-a-hard-cap (cloud.google.com/billing).
Researched 2026-06.
