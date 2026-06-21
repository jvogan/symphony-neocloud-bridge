# Provider-Backed Workload Lessons

Use these rules when a RunPod run is more than a tiny smoke: multi-stage, scientific, analytical, fanout, GPU-heavy, volume-backed, or expensive enough that a silent failure would waste operator time.

## Contract Truth

- Validate the real route, not the flags. Prove input materialized, target/index/worklist built, exact tool ran against the target, and outputs join back to the intended dataset.
- Do not let package checks pass live readiness. `which`, `--version`, `pip show`, and `conda list` are preflight only. Paid readiness needs exact executable proof plus artifact-level validation.
- A provider state is intent, not truth. `RUNNING`, billable time, pod allocation, runtime metrics, and command submission do not prove the workload is productive.
- Use a tiny real smoke before scale. It must use the same provider, image class, route, artifact retrieval path, hash checks, and cleanup path.

## Evidence Shape

- Split outputs into primary evidence, context evidence, and dossier material when the domain has analytical claims. Context/reference/control hits are not primary discoveries.
- Declare claim levels explicitly: `artifact_execution_only`, `unsupported`, `observed`, `inferred`, `candidate`, or `validated`.
- Normalize raw tool output into stable ledgers with IDs, provenance, evidence labels, and source paths. Raw tool files alone are rarely a closeout artifact.
- Controls are gates, not discoveries. Positive and negative controls can support maturity, but they must not be promoted as target findings.

## Scale Gates

- Add a cardinality or fanout gate before expensive launch. Estimate shard count, query count, target count, expected output size, and budget behavior when the estimate exceeds policy.
- Annotate once and join many when possible. Avoid repeating expensive annotation or model steps for every downstream report if one normalized ledger can be reused.
- Keep raw or bulky data on the declared durable storage plane. Pull compact ledgers, summaries, figures, hashes, and closeout packets to the operator side.
- For network volumes or checkpoints, require input hashes before trusting done markers. Persistent volumes are useful, but stale sentinels can make a new run look complete.

## Fallbacks And Partial Success

- No silent fallback. If execution falls back from provider to local, full to rescue scope, live to mock, or primary source to reference-only, close as degraded or partial unless artifacts prove the original contract.
- Write a partial summary on failure, timeout, interrupt, or degraded rescue. The summary should name completed stages, missing stages, usable artifacts, and next safe action.
- Treat orchestration success separately from workload success. Dispatch, pod creation, cleanup, and Linear closeout can succeed even when the workload failed or only partially completed.
- Make stages resumable and idempotent, but write done markers only after outputs validate.

## Worker Behavior

- Audit known inputs before asking the operator. Read the issue, manifest, ledgers, existing run records, and artifact directories first.
- Use deterministic watchers for polling, cleanup, and cost records. Human or agent attention is a cost; design the bridge to make waiting boring and bounded.
- Report what is known and unknown. If there is no heartbeat, log tail, HTTP status, SSH peek, or fetched status packet, say that productivity is unproven.
