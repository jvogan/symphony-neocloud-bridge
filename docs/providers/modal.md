# Modal Provider Reference

## Overview / positioning

Modal (modal.com) is a serverless GPU/CPU **function** platform. In our taxonomy it is
`provider_class: serverless_function` and is **adapter-ready, not a blessed scale path**:
use it for bounded single-function GPU canaries or small single-container fanouts — not for
large multi-shard scale (that is AWS Batch) or long iterative pod work (that is RunPod).
Validate it with a public smoke before treating it as an launch guide.

The cost model is the key differentiator from pod providers: Modal bills **per-invocation /
per-runtime-second**, not per-uptime-hour. That makes it attractive for a *single heavy,
high-RAM job* while per-hour pods stay cheaper for *many small iterative jobs*. A
multi-provider bridge must expose this **per-invocation-vs-per-uptime axis** explicitly so a
planner can route by job shape.

The default implementation pattern is **one long-lived `@app.function` that runs a sequential
in-container fanout with model reuse**, committing an artifact Volume every N units, followed
by a **host** process that fetches the Volume, validates + hashes artifacts, ranks, proves
cleanup, and reports cost. There is **no `.spawn()` / `FunctionCall` polling and no `.map()`**
in the initial bridge design; fanout is a Python loop inside a single pinned container until
a public smoke validates another shape.

**Cross-provider rule (applies to every adapter):** provider RUNNING, a clean exit-0, app
logs, a returned summary dict, or high provider confidence is **NEVER** workload success. A
real closeout requires fetched + content-verified + hashed artifacts, a tag-scoped billing
report, and verified cleanup. Rank and claim results only from host-validated artifacts.

## Launch & command rendering

There is **no separate "startup script" surface** like a pod entrypoint. The **Image build
chain is the startup spec** and the `@app.function` body is the command.

Three rendered surfaces:

1. **Image build chain (startup).** Base (`micromamba` / `debian_slim`) →
   `run_commands("apt update && apt install -y ...")` → `micromamba_install(...)` →
   `uv_pip_install(<pinned, including "pkg @ git+https://...@<REV>">)` →
   `add_local_dir(local, remote_path="/root/<pkg>", copy=True)` → `env({...})`.
   Pin **every** git/pip/conda dependency by revision.
2. **Source / code mount.** Local sibling packages via
   `Image.add_local_dir(local_dir, remote_path="/root/<pkg>", copy=True)`; git deps via
   `uv_pip_install("pkg @ git+https://...@<REV>")`. The launch manifest records a `git_ref`
   that **must be pinned to an immutable commit** before real launch.
3. **Command.** `@app.local_entrypoint()` calls `fn.remote(run_id=..., wave=..., ...)`
   (blocking, returns the summary dict). **Args carry run identity; tags carry billing
   identity.**

Canonical module-top shape:

```python
app = modal.App(
    name=APP_NAME,                 # declared display name, e.g. "<prefix>-<task>-canary"
    tags={                         # tags drive billing + closeout filtering
        "campaign": CAMPAIGN_ID,
        "provider": "modal",
        "run_id": os.environ.get("RUN_ID", "pending"),
        "wave":    os.environ.get("WAVE", "fanout"),
    },
)

image = (
    modal.Image.micromamba(python_version="3.12")
    .run_commands("apt update && apt install -y git build-essential")
    .micromamba_install("pkg=ver", channels=["conda-forge"])
    .uv_pip_install("tool @ git+https://github.com/org/tool.git@<REV>", "lib==x.y.z")
    .add_local_dir(APP_DIR / "payload", remote_path="/root/payload", copy=True)
    .env({"HF_HOME": "/models", "CUBLAS_WORKSPACE_CONFIG": ":4096:8"})
)

models_volume    = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)
artifacts_volume = modal.Volume.from_name(ARTIFACT_VOLUME_NAME, create_if_missing=True)

@app.function(
    image=image,
    volumes={MODEL_DIR: models_volume, ARTIFACT_DIR: artifacts_volume},
    gpu="H100",            # or "L40S" / "A100-80GB"
    timeout=2400,          # hard wall-clock cap (seconds)
    startup_timeout=600,   # cold-start / image-build allowance
    max_containers=1,      # pin to ONE container; fanout is in-process
    retries=0,             # no silent paid retries
)
def fanout(run_id: str, max_candidates: int = 24) -> dict: ...

@app.local_entrypoint()
def main(run_id: str, wave: str = "fanout"):
    result = fanout.remote(run_id=run_id)   # blocking; returns summary dict
    print(json.dumps(result, indent=2, sort_keys=True))
```

Launch via the credential wrapper:

```bash
modal-kc run modal/apps/<app>.py --run-id <run_id> --wave <wave>
```

**Launch gotchas (load-bearing):**

- **Do NOT pass `modal run --name <run_id>`.** `--name` *overrides the declared `modal.App`
  display name*. Omit it when the source already declares the App name; pass run IDs through
  **args + tags** instead.
- **Capture the `ap-*` app ID from launch stdout IMMEDIATELY.** Ephemeral `modal run` apps
  stop resolving **by name** after teardown; logs / history / billing must then be fetched by
  app ID. Preserve `modal-run.stdout.log` as the full function log.
- **Do not derive repo root from a fixed `Path(__file__).parents[N]`.** Remote modules mount
  as `/root/<app>.py`. Walk up for a **marker dir** instead. Ship sibling helper packages with
  `Image.add_local_dir(local, remote_path=..., copy=True)`.
- **Single-GPU device pinning:** single-GPU containers need an explicit device index. Tools
  that default to a high index (e.g. `--gpu 7`) fail on a 1-GPU run (checkpoint deserialization
  onto a missing CUDA device). Always pass `--gpu 0`.
- **GPU memory recipe:** batch-size-2 fanouts hit CUDA OOM. Robust recipe is batch-size 1 +
  `torch.cuda.empty_cache()` between units + partial-fallback-into-scoring once ≥N units exist.
- **Dependency-pin traps:** pinning one package can force-downgrade another (e.g. an editable
  install upgrading a numeric library past a consumer's ceiling mid-run — re-pin **inside** the
  consuming stage). A base install can pull an ABI-incompatible companion wheel (force-reinstall
  the matching `+cuXXX` wheel with `--no-deps`). Image-build / runtime-gate retries cost real
  money and must be counted in campaign totals.
- **Fail-closed preflight:** gate real launch behind an env flag (e.g.
  `<PREFIX>_MODAL_LAUNCH_ALLOWED=1`). Static preflight must **not** call Modal APIs, launch
  compute, or print secrets.

## Authentication

**Never put Modal tokens in a repo, `.env`, issue tracker, logs, or artifacts. Never use
`modal.Secret` for the account token in code.** Credentials come from a Keychain-backed
wrapper:

- Wrapper: a Keychain-backed `modal` wrapper, e.g. `modal-kc` — invoke it as `modal-kc <args>`,
  exactly like the `modal` CLI.
- It loads `token-id` / `token-secret` from a **macOS Keychain service** (e.g. `modal-keychain`),
  accounts `modal-token-id` / `modal-token-secret`.
- Repo / tracker / logs / artifacts may name **only** the wrapper command and the Keychain
  service/account names — never the token values.

In-function workload secrets (e.g. a weights-cache `HF_HOME`) should be handled by
**Volumes and `Image.env(...)`**, not `modal.Secret`, unless the workload explicitly
needs a provider secret.
If a workload genuinely needs a `modal.Secret`, it must be referenced **by name only** and
provisioned out-of-band.

## Monitoring / observability

### NON-authoritative as success

**Modal app logs and function completion are NOT workload success.** A clean process exit, a
returned summary dict, or high provider confidence is **intent / provider confidence only**.
Claim results only from host-validated artifacts.

### Authoritative provider-side observation

Capture all of these into the run's provider-artifact root:

```bash
# Logs with identifiers (prefer app ID after teardown)
modal-kc app logs <app-name-or-ap-id> --timestamps \
    --show-function-id --show-function-call-id --show-container-id

# App / history status JSON
modal-kc app history <app-name-or-ap-id> --json
modal-kc app list --json          # also used for zero-task cleanup proof
```

`app logs` / `app history` **by name can fail after ephemeral app teardown** — which is why
the `ap-*` ID must be captured at launch.

### In-container progress (so a timeout still leaves evidence)

There is **no long-poll handle** — `.remote()` is synchronous/blocking. Live "state" is the
Volume-backed heartbeat plus `app list` / `app history`. The function therefore writes durable
progress to the **artifact Volume** as it goes and commits frequently:

- Append heartbeat events to `stage-progress.jsonl`.
- Rewrite `run-summary.json` / `artifact_index.json` after each unit.
- `artifacts_volume.commit()` every N units **and in the `except` path**, so a timeout/crash
  still leaves partial evidence.
- On exception, write `partial-summary.json` (`completed_stages`, `completed_units`,
  `failed_stage`, `resume_command`, `error`, `traceback_tail`), commit the Volume, **then
  re-raise**.

Runtime probes need **long timeouts** and must emit JSON diagnostics on timeout/error rather
than hang (first-use `--help` of a heavy tool can exceed 30s).

## Durable artifact egress

Artifacts live on the committed Modal **artifact Volume**; durable egress is a **host-side
fetch + hash** step, not a provider feature.

- **Recursive `modal volume get` is UNRELIABLE** (fails `Is a directory` / mishandles run
  roots). Reliable fallback: `modal-kc volume ls <run-root>` (and each subdir), then **explicit
  one-file pulls**.
- Verify file **content**, not just existence: structured outputs (e.g. PDBs) must begin with
  expected magic (`ATOM` / `HEADER`); reject placeholder `repr()` strings. Headers can be
  long, so **scan beyond the first few KB**.
- Compute **SHA-256** for every fetched artifact into a hash ledger / `artifact_index.json`.
- **Exclude post-closeout validation files from the hash ledger** (or hash before generating
  them); otherwise a valid late closeout check makes the ledger look stale.
- Fetched artifacts land under a host path like
  `.runtime/provider-artifacts/<run_id>/modal_job_v1/...`.

Expected artifact set (from the manifest): `stage-progress.jsonl`, `executed-commands.jsonl`,
`validation/input-audit.json`, `validation/host_probe.json`,
`validation/modal-provider-check.json`, `<domain>-scores.json`, `claim_ledger.json`,
`artifact_index.json`, `modal-cost-report.json`, `modal-cleanup-proof.json`,
`partial-summary.json`.

## Cost & budget control

Budget is declared in the provider profile / manifest and enforced as a wave cap:

```json
"budget": { "max_authorized_spend_usd": 3, "max_runtime_minutes": 15, "canary_only": true }
```

Wall-clock is bounded by `timeout` / `startup_timeout` on the function; spend is bounded by the
declared wave cap ($2–$10 typical) plus operator gating. `retries=0` and `max_containers=1`
bound paid concurrency.

**Billing CLI that works:**

```bash
modal-kc billing report --for today --resolution h --tz local \
    --tag-names campaign,run_id --json
```

then filter locally by tag. **The older `modal billing --tag ...` syntax is INVALID** — some
manifests still carry the stale `billing --tag` command; treat the `billing report
--tag-names` form as correct and **normalize stale commands** rather than trusting manifest
strings verbatim.

- Keep **both** `run_cost_usd` (did this wave stay under cap?) and the **campaign-day
  `total_cost_usd`** (historical context) in closeout.
- **Hourly rows LAG.** If the run row is not yet exposed at closeout, set
  `run_billing_status=billing_pending` and rely on the cleanup / zero-task proof; captured
  campaign rows should still show well under cap.
- **Captured totals must include failed canaries** — image-build failures, import failures, and
  runtime-gate retries are real billing rows. A failed wave can produce **zero** valid outputs
  while still spending against the cap, so failure cost must be bounded explicitly.

**Planning economics:** small canaries have worse per-unit economics because fixed
model-load/startup overhead dominates. Larger waves amortize startup better, but they must
still carry hard `timeout`, `max_containers`, and spend ceilings. Size `timeout` to the
workload and treat timeout failures as paid validation failures, not free retries.

## Cleanup / closeout

```bash
modal-kc app stop <app-name-or-ap-id>
modal-kc app list --json     # zero-task evidence
```

- **`app stop` by ID can return nonzero** because an ephemeral local-run app is already gone
  after teardown. In that case cleanup proof relies on **`app list` zero-task evidence +
  terminal run state**, not on the stop command's exit code.
- Record cleanup proof to `modal-cleanup-proof.json` and keep run billing, cleanup proof,
  `app list` proof, and the host-side provider-closeout check together in the provider-artifact
  root.

A real closeout packet = provider logs (timestamps + function/container IDs) + app/history
status JSON + tag-scoped billing report + committed Volume artifact tree fetched locally +
SHA-256 hashes + app-stop / zero-task cleanup proof + a parseable outcome. **Rank from
host-validated artifacts.**

## Adapter implications

- **validate-manifest:** Require `provider=modal`, `provider_class=serverless_function`,
  `app_name`, `function_name`, `entrypoint`, `volume_name` + `volume_mount`,
  `requires_volume_commit`,
  `resources{gpu,gpu_count,max_containers,timeout_seconds,startup_timeout_seconds,retries}`,
  `budget{max_authorized_spend_usd,max_runtime_minutes,canary_only}`, and `required_tags`
  including `campaign`, `provider`, `run_id`. **Reject** if `git_ref` is unpinned for a real
  launch, or if any token value appears in the manifest.
- **prepare:** Resolve the `modal-kc` wrapper + its Keychain service; build/validate the Image
  chain (pinned base + apt + conda + pip-by-revision + `add_local_dir(copy=True)` + `env`);
  ensure both named Volumes exist (`from_name(create_if_missing=True)`). Fail-closed unless an
  explicit launch-allowed env flag is set; static prepare must **not** call Modal APIs or print
  secrets.
- **write-handoff:** Carry `app_name`, `entrypoint`, args (`run_id`, `wave`, caps),
  `required_env`, tags, and budget.
- **validate-handoff:** Assert `--name` is **NOT** used at launch (it overrides the App display
  name) and that the `ap-*` app ID **will be captured** from launch stdout.
- **render-startup:** Emit the **Image build chain** as startup +
  `@app.function(... gpu, timeout, startup_timeout, max_containers=1, retries=0)` +
  `local_entrypoint` calling `fn.remote(...)`. Pin all deps; re-pin transitive conflicts inside
  the consuming stage. Inject `--gpu 0` for single-GPU device pinning.
- **source-checkout:** Local sibling packages via `Image.add_local_dir(remote_path=...,
  copy=True)`; git deps via `uv_pip_install("pkg @ git+...@<REV>")`. In-container code must find
  repo root by **marker-dir walk**, not fixed parent depth.
- **poll-state:** No long-poll handle (`.remote()` is synchronous/blocking). "State" =
  `app list --json` (running / zero-task) + `app history --json` + the Volume-backed
  `stage-progress.jsonl` heartbeats. There is **no `.spawn()` / `FunctionCall` polling** in the
  default path.
- **capture-evidence:** Heartbeats appended to `stage-progress.jsonl` and committed on the
  artifact Volume; `run-summary.json` rewritten per unit;
  `app logs --timestamps --show-function-id --show-function-call-id --show-container-id`
  (by **app ID** after teardown); preserve `modal-run.stdout.log`. Host hashes every fetched
  file into `artifact_index.json` / hash ledger; verify magic-prefixed files (scan past first
  KB); **exclude post-closeout files** from the ledger.
- **egress-plan:** Egress = host fetch from the committed Volume. **Do not rely on recursive
  `volume get`**; use `volume ls` + explicit one-file pulls. Proof = local artifact tree +
  hashes under `provider-artifacts/<run_id>/`.
- **budget-limits:** Encode `max_authorized_spend_usd` + `max_runtime_minutes` + `canary_only`;
  enforce wall-clock via `timeout` / `startup_timeout`; set `retries=0`, `max_containers=1` to
  bound paid concurrency.
- **billing-report:** Use `modal-kc billing report --for today --resolution h --tz local
  --tag-names campaign,run_id --json` (**NOT** `billing --tag`). Emit both `run_cost_usd` and
  the campaign-day total; support `run_billing_status=billing_pending` when hourly rows lag;
  **include failed-canary cost**.
- **cleanup-closeout:** `app stop <ap-id>` then `app list --json` for zero-task proof; treat a
  nonzero `app stop` exit as **expected** for ephemeral apps and fall back to list-based proof;
  write `modal-cleanup-proof.json`.
- **supervise:** Drive the run from host-side state (`app list` / `app history` + Volume
  heartbeats), not from the blocking `.remote()` return. Enforce the wall-clock cap and the
  fail-closed launch flag; size `timeout` to the workload (timeouts have produced paid runs with
  zero usable output).
- **dashboard:** Surface run/app IDs, current `app list` task count, latest `stage-progress`
  heartbeat, `run_cost_usd` vs cap (or `billing_pending`), and cleanup state. Make the
  **per-invocation-vs-per-uptime** cost axis visible so routing decisions are legible.
- **symphony-outcome:** Derive the outcome from **host-validated artifacts + cleanup proof +
  cost report**, never provider confidence. The outcome must carry: run/app IDs, artifact
  hashes, run + campaign cost (or `billing_pending`), cleanup state, and a claim-ceiling string.
  Preserve provider-native stage events and only **add** host-side provider-check/closeout
  events (never overwrite tool-native stages with generic labels).

## Open questions

- **Billing-row latency is unquantified.** We only know hourly rows sometimes lag past
  closeout. The adapter must tolerate `billing_pending` indefinitely. Is there a per-call cost
  field on the returned run / `FunctionCall` object that would avoid the hourly-report
  dependency?
- **Concurrent / async fanout** (`.spawn()`, `FunctionCall` polling, `.map()`,
  `max_containers>1`) needs separate validation. Keep `max_containers=1` with an
  in-process loop until state polling / cost attribution / cleanup for true multi-container apps is
  untested.
- **`modal.Secret`** for genuine in-workload secrets (vs. the `modal-kc` account wrapper) was
  not validated end-to-end. Default to Volumes + `Image.env`; if a workload needs
  `modal.Secret`, keep only reference names in the manifest.
- **Non-ephemeral deployed apps** (`modal deploy` + persistent endpoints / web functions) are
  out of scope; the provider setup entry assumes `modal run` ephemeral apps where name resolution can
  die after teardown.
- **CPU-only / high-RAM jobs** (the ~336 GiB, ~$0.44-per-run, $30/mo-free sizing profile) were
  planning estimates only. RAM ceilings, CPU pricing, and free-tier behavior need public
  validation before use.
- **`modal volume get` reliability** boundary is fuzzy: it fails on directories / run-roots, but
  the precise failure modes (depth, glob, large files) aren't characterized — the safe path is
  always `ls` + per-file pulls.
- **Stale manifest command drift:** at least one launch manifest still encodes the invalid
  `billing --tag` command and a name-based logs command. The adapter should normalize / validate
  these rather than trust manifest command strings verbatim.
