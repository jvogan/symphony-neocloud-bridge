# RunPod Provider Reference

Focused supplement to the deep RunPod docs already in this repo
(`runpod-observability-ladder.md`, `runpod-status-taxonomy.md`,
`runpod-worker-readiness.md`, `runpod-official-surfaces.md`,
`runpod-superpowers-2026-05.md`, `private-source-storage-runbook.md`). Those own
the canonical lifecycle: "provider state is intent, not progress," crash-loop /
negative-uptime detection, the ~65KB payload guard, CPU container-disk caps,
proxy lag, network-volume S3 read-without-compute, presigned upload, cost
centers, and the MCP log/exec gaps. **Read them first; this file does not
duplicate them.** It records only the non-obvious operational deltas needed by
the public bridge contract.

**Cross-provider invariant (holds for every adapter):** provider `RUNNING`,
`exit_code=0`, and healthy app-logs are **never** workload success. Closeout
requires fetched + SHA-256-hashed durable artifacts, a billing reconciliation,
and verified cleanup (no pods left under the run's name prefix). On RunPod the
clean-exit auto-restart loop (below) makes "looks done" especially deceptive.

## Overview / positioning

- **Pods for first executions; Serverless/Flash only after a workload is
  "boring."** Promote to Serverless once pod runs are pure handler-shaped
  functions. Interactive debugging, long notebooks, and uncontrolled write
  concurrency are poor Serverless candidates. The Flash/Serverless adapters are
  scoped elsewhere; this is the promotion gate, not the launch default.
- **Control-plane rule: use REST `POST /pods` (`https://rest.runpod.io/v1`), not
  the MCP `create-pod` tool.** MCP `create-pod` does **not** support
  `dockerStartCmd`, `networkVolumeId`, or `computeType: CPU` — so it cannot
  launch any real bridge workload. Stop = `POST /v1/pods/<id>/stop`,
  delete = `DELETE /v1/pods/<id>`, volume create = `POST /v1/networkvolumes`,
  registry auth = `POST /v1/containerregistryauth`.

## Launch & command rendering

### Instance selection
- **`cpuFlavorIds` enum is exactly `{cpu3c,cpu3g,cpu3m,cpu5c,cpu5g,cpu5m}`**
  (3rd/5th gen; `c`=compute, `g`=general, `m`=memory). Widen capacity by passing
  **multiple** flavors (`['cpu5c','cpu5g','cpu3c','cpu3g']`) rather than dropping
  to Community Cloud — network volumes are Secure-only.
- **Canonical working CPU recipe:** `cpuFlavorIds=['cpu5c']`, `vcpuCount=4`,
  Secure Cloud, stock `python:3.12-slim`. The `cpu5g` and community-CPU flavors
  repeatedly reach provider `RUNNING` but never expose runtime metrics, public
  IPs, or artifact ports ("provider-alive / workload-unproven") — prefer `cpu5c`.
- **`gpuTypeIds: []` (unpinned) biases the scheduler toward the most expensive,
  most oversubscribed cards** (H100/A100) — exactly the ones most likely to
  wedge or be capacity-rejected. Pin mid-range cards on Secure
  (RTX A4000/A4500/A5000/4000-Ada, ~$0.16-0.18/hr) for healthier hosts and lower
  cost.
- **Anomaly: Secure + `networkVolumeId` set sometimes ignores `cpuFlavorIds` and
  allocates a GPU-class machine anyway** (Blackwell-class hourly rates). The
  field is documented but not always honored — verify the *actual* allocation and
  budget for the surprise.
- **Empty `dataCenterIds` means "any DC" to the API, but the bridge
  `gpu-catalog` pre-check treats a GPU whose catalog `datacenters` array is `[]`
  as "no offered DC" and blocks create — a false positive.** Escape via
  `RUNPOD_BRIDGE_SKIP_CATALOG_CHECK=1` (`runpod_catalog.py::should_check_gpu_catalog`)
  or explicitly populate `dataCenterIds`. The pre-check is intentionally more
  conservative than the API.

### Payload / startup rendering (beyond the ~64KB guard)
- **HTTP 500 `"no longer any instances available with the requested
  specifications"` is ambiguous** — the *same* error fires for a genuine capacity
  outage *and* for a body over the ~64KB cap. **Disambiguate by re-firing the
  identical specs with a trivial `dockerStartCmd`
  (`['bash','-lc','echo hi; sleep 60']`); if that succeeds it was payload size,
  not capacity.**
- **Compress with gzip, never xz.** `gzip+base64` (decode via
  `base64 -d | gunzip`) is portable; shell/Python source compresses ~3-4x.
  `xz`/`lzma` crash-loops on `condaforge/miniforge3` / `mambaforge` base images,
  which lack `xz-utils`. Use `tar+lzma` only on images known to ship `xz`;
  otherwise tar+gzip. **AST-strip docstrings/comments** to fit when close (one
  bundle landed at 59,996 bytes with 1.4KB headroom).
- **No inline heredocs inside `dockerStartCmd`** — they silently crash-loop
  within 60-90s (~36KB). Base64-decode the real boot script into the workdir and
  `exec` it. Keep the boot *wrapper* tiny (<2KB): decode payload, start the
  artifact HTTP server early, `exec boot.sh`, capture rc, write a sentinel, hold.
- The empirical body ceiling is undocumented (~61,440 observed as a bridge hard
  limit; ~64KB cited elsewhere) — keep the guard conservative.

## Authentication

- **RunPod injects a STALE `RUNPOD_API_KEY` into the pod that does NOT match the
  caller's key.** Any in-pod self-stop via the default `RUNPOD_API_KEY` env var
  **always 403s.** Two fixes: (a) pass a **non-default** env var name at create
  (e.g. `BRIDGE_STOP_KEY`) and read self-stop creds from that; or (b) **canonical**
  — make self-stop operator-side: the boot script just `sleep IDLE_SECONDS` then
  exits, and the orchestrator pulls artifacts and stops the pod externally.
- **Private image pulls require a one-time container registry auth**
  (`POST /v1/containerregistryauth {name,username,password}` → `id`, then
  `containerRegistryAuthId` at create). Without it **pod creation succeeds
  silently and the container never starts** — no "unauthorized" surfaces through
  `desiredStatus`. **Pre-verify registry access from a logged-in Docker client:**
  `docker manifest inspect <registry>/<owner>/<img>:<tag>` expecting a successful
  manifest response.
- **Multi-arch digest trap:** a digest copied from a CI workflow summary is the
  top-level **multi-arch index**, not the `linux/amd64` runtime child manifest.
  Pinning the index (or an attestation / unknown-platform descriptor) makes
  RunPod **stall before any log line**. Rebuild with `provenance:false` /
  `sbom:false`, pin the `linux/amd64` child digest, and inspect via
  `docker buildx imagetools inspect` or a registry HEAD instead of pulling
  locally. Large images (>5GB) stall on pull for 30+ min with uptime
  null/negative.

## Monitoring / observability

The observability ladder and status taxonomy are authoritative for the general
"intent vs progress" model. Two **distinct provider-lifecycle failure shapes**
to add, plus monitoring gotchas:

1. **Wedged host (docker daemon never starts the container).** Symptoms:
   `desiredStatus=RUNNING`, `uptimeInSeconds` pinned at 0-1s *forever*, 0% util,
   sshd never binds, no runtime metrics, no proxy response. **It bills the whole
   time.** This is *not* a crash-loop (uptime never even rises) and *not* "slow
   boot." **Rule: stop within 10-15 min of confirmed wedge signals.**
2. **Container auto-restart "succeed-loop" (the big one).** **RunPod's scheduler
   restarts the container on a *clean* exit (`exit_code=0`), not just on
   failure.** A workload that finishes, holds for inspection, then exits 0 gets
   restarted ~2 min later and re-runs from the top — visually identical to a
   crash-loop, silently racking up per-cycle cost. **There is no `restartPolicy` field** in
   REST `PodCreateInput` or the bridge create body; restart behavior is hardcoded
   in RunPod's scheduler. Mitigations:
   - **Sentinel short-circuit at the very top of the workload:**
     `if [ -f /workspace/repo/runpod-execution/.cycle-complete ]; then sleep 600; exit 0; fi`.
     The bridge has `terminal_hold` (`sleep_infinity`/`seconds` in `startup.py`)
     but **no `.cycle-complete` short-circuit guard** — a concrete adapter gap.
   - **Or externally stop the pod the instant the success signal fires**
     (operator-side; ties back to the stale-key fix).
   - `set -e` / `pipefail` + auto-restart is especially nasty: any non-zero
     command aborts the script and triggers a full restart, *hiding where it
     crashed*. Wrap fallible steps; consider `set +e` + per-command markers.

Monitoring gotchas (new vs the existing lag/404 notes):
- **Proxy responses are cached for minutes.** A "stuck pipeline" is often a
  "stuck cache." **Append `?cb=$RANDOM` to every monitor GET** against
  `https://<pod-id>-<port>.proxy.runpod.net/...`.
- **`runtime:null` immediately after deleting another pod on the same machine**
  is the scheduler holding the request during prior-machine cleanup — do **not**
  escalate as boot failure for the first ~8-10 min if a prior pod was just
  deleted.
- **No-growth detector:** beyond state-change checks, HEAD the critical-path
  output file sizes and alert after **3 consecutive zero-growth heartbeats** — a
  hung process holds `RUNNING` indefinitely. (CPU=0% during heavy
  conda/pip install is normal — network-bound resolver, not a hang.)
- **Restart thresholds defined before any signal:** 1 = noise, 2 = suspect,
  3+ = stop.

### What is NON-authoritative as success
- `desiredStatus=RUNNING` / "intent" state — only intent, never progress.
- `uptimeInSeconds` rising — necessary, not sufficient (a succeed-loop re-runs
  with healthy uptime).
- `exit_code=0` — on RunPod this *triggers a restart*, so it is anti-signal as
  much as signal.
- HTTP 200 from the proxy — can be a cached page or an HTML error page (sniff,
  below).
- App logs showing "done" — the orchestrator may never see the artifact because
  logs are wiped on stop. Success = fetched + hashed artifact, billing recorded,
  cleanup verified.

### GPU / driver / CUDA compatibility matrix (entirely new)
Host driver pins max CUDA, which pins max framework version. **2026-05
observations; will drift — re-verify per campaign.**

| Card (Secure)              | Driver | Max CUDA | Implication |
| -------------------------- | ------ | -------- | ----------- |
| RTX 4090                   | 550.x  | 12.4     | vLLM <= 0.8.5, torch <= 2.6+cu124. Pin a 2.4.0 / cuda12.4.1 devel image. |
| L40S / 6000-Ada / H100     | 570.x  | 12.6+    | vLLM 0.9+, torch 2.7+cu126. |
| RTX 3090 / 5090            | —      | 12.8     | CUDA 12.8 only here. |

- **FP8 models require Ada Lovelace / Hopper (SM89+).** On Ampere (SM86) vLLM
  engine init fails with `fp8e4nv not supported in this architecture`.
  **`--dtype bfloat16` does NOT dequantize FP8 weights at load** — it still fails;
  switch to a bf16-native dense model on Ampere.
- **Conda-CUDA-vs-host-driver mismatch (PTX JIT failure):** conda-installed
  CUDA/torch can compile kernels the pod's driver can't load. Keep a
  platform-fallback loop (`CUDA → OpenCL → CPU`) or, better, **reuse the provider
  base image's preinstalled CUDA torch** (`runpod/pytorch:*`) and pip-install only
  the app layer — avoids the mismatch and cuts bootstrap time.
- **A mistyped/non-existent base image tag** leaves the pod wedged with no clear
  error. Verify tags before launch.
- **First action on every GPU pod:** `nvidia-smi` + a `torch.cuda.is_available()`
  probe before any work — fail fast if no usable GPU was provisioned.

### Stock-image landmines (slim / conda base images)
Base images drift behind your install lines; **run a `command -v` smoke pod
(cents, <3 min) checking every binary the real pipeline calls before any paid
run.** A cheap binary smoke should catch missing tools before an expensive workload
crash-loops.
- **`python:3.11-slim` / `python:3.12-slim` lack `libgomp.so.1`** → anything
  OpenMP dies with `OSError: libgomp.so.1: cannot open shared object file`;
  `apt-get install -y --no-install-recommends libgomp1` is required. `free` and
  other diagnostics are also absent.
- **`condaforge/miniforge3` / `mambaforge` ship only python+conda — no `curl`,
  `wget`, or `xz-utils`.** Downloads must use Python `urllib` with a Mozilla
  User-Agent (some hosts block the default UA), streaming in 64KB chunks with
  sha256 verify + retry.
- **Sentinel smoke checks must anchor on version digits / unique output**
  (e.g. `^# TOOL 3\.4`, `^toolname version 2\.[12]\.`), **not the bare tool
  name** — a name-only sentinel matches `bash: line N: <tool>: command not found`
  and yields a **false PASS**. Demote `sentinel=true` to `suspect_false_pass`
  when `exit_code!=0` or output contains
  `command not found|No such file|ModuleNotFoundError`.
- **Thread hygiene for CPU pods:** export
  `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1`
  to stop thread-thrashing; set `PYTHONPATH` for source-tree imports.
- **`pip` installs do NOT persist across pod stop/start — only `/workspace`
  does.** Every resume pays a ~4-min reinstall tax. Fixes: a `uv venv` *on*
  `/workspace`, a custom image with deps baked, or a Network Volume.

## Durable artifact egress

The durable-egress modes (network volume, network-volume S3, SCP, presigned,
object-store) and "proxy/archive is smoke-only" are already documented. Adds:
- **Provision RunPod S3 access keys during operator prep, not at closeout.** The
  network-volume S3 API (`https://s3api-<dc>.runpod.io/<volume-id>/<path>`,
  separate keys, *not* `RUNPOD_API_KEY`) reads a retained volume with **no running
  compute** — summary fetch never depends on pod capacity. Strongly preferred over
  a second pull-pod on the same volume. The bridge already renders these
  endpoints (`egress.py`, `runpod_s3_verify.py`).
- **Pull-back pattern when self-stop is unreliable:** after `pipeline_exit=0`,
  spin a short-lived lowest-cost pull pod running `http.server` on the same
  volume, verify it actually started, fetch **only compact derived artifacts**
  (TSV/JSON/HTML/xlsx), never bulk raw data. **Use STOP (preserves container disk,
  billed at storage rate ~$0.01-0.05/hr), not DELETE, until after verification.**
- **Start the artifact HTTP server EARLY (before heavy install)** so it is
  reachable for the whole run; expose its port in `ports` (e.g. `8000/http`).
  Live progress server is a *separate* port (e.g. `8001`).
- **Artifact integrity must byte-sniff, not trust HTTP 200.** A proxy can return
  an HTML 404/502 error page with HTTP 200; a "successful" pull can be a 404 page.
  Sniff for `<html`, `<!doctype html`, `404 not found`, `502 bad gateway`,
  `503 service unavailable`, reject `bytes<=0`, then verify the declared sha256.
- **Logs do NOT survive pod stop.** Container disk is wiped; there is no
  post-stop log endpoint; the in-pod artifact server only serves logs *on success
  during the hold*. For post-mortem on crash-prone runs, **write logs to a Network
  Volume `/workspace`** (survives exit) or keep an independent forensic channel
  (SSH key on hand, or an auth-gated progress server).
- **Reference-data download bandwidth on RunPod is throttled (~1-4 MB/s)** —
  pre-stage large reference data to the volume or pull from same-region cloud
  open-data mirrors.

## Cost & budget control (what's actually enforced)

- **`--max-spend-usd` / `budget.max_estimated_cost_usd` is ADVISORY and NOT
  enforced at runtime.** The only **hard** backstop RunPod enforces is
  `terminate_after_minutes` (renders to `--terminate-after`). **Always set a tight
  `terminate_after_minutes = timeout + 15` as the true cost cap / budget-burn
  watchdog.** The auto-restart succeed-loop blows straight past any advisory cap.
- **Pod-time bills wall-clock from create to delete**, so slow teardown is real
  money — one pod billed $0.0226 (886s pod-time) for a 20s workload purely from
  delayed local polling/cleanup after artifacts were already fetched. **Pull
  artifacts and delete promptly.**
- **Billing API lags wall-clock; use wall-clock (`runtime × costPerHr`) for live
  budget tracking**, reconcile against `GET /billing/pods` later, and always mark
  provider-estimated cost as `estimate`.
- **Failed create requests rejected before a pod spins up cost $0** — only created
  pods bill. Canary discipline keeps failed-shard debugging cheap.
- **Warm-image amortization is real:** a later wave was cheaper per successful
  shard than an identical earlier wave purely because the image was already warm
  in the datacenter. **Reuse one digest-pinned image across a campaign.**
- **Volume-vs-redownload math:** for public, periodically-updated datasets,
  refetching across a few waves (~$0.21) beat a 100GB Secure volume over 2 weeks
  (~$3.50, ~17x cheaper to refetch). **Network Volumes (~$0.07-0.10/GB-month) only
  pay off for proprietary/custom-built indexes or genuine reuse/resumability.**
- **Indicative pricing (2026-05, sizing only):** CPU `cpu3c` ~$0.06/hr; CPU smokes
  ~$0.046-0.092/hr; RTX 4090 ~$0.69/hr;
  L40S ~$0.86/hr; A100-80 PCIe ~$1.39/hr; A100 SXM ~$1.89-3.20/hr;
  H100 ~$2.79-4.50/hr.
- **H100 SXM has only ~188GB RAM** — *not* a safe carrier for >256GB workloads
  even though it dodges CPU-capacity walls. **Pick a carrier by RAM ceiling, not
  GPU.** Watch host OOM-kills (a 49-min run was killed before writing results);
  gate heavy work behind a local RAM/wall probe first.

## Cleanup / closeout (verified, not fire-and-forget)

- **Cleanup is a *verified* step.** After artifact fetch: delete (or stop) the
  pod, then run a **post-cleanup prefix check** confirming **no pods remain under
  the run's `resource_name_prefix`**. Success requires
  `verification_ok AND cleanup_ok AND active_count==0`. (Bridge already enforces
  this; reiterated because the auto-restart loop makes "fire-and-forget stop"
  actively dangerous.)
- **Shared-account safety:** RunPod accounts are often shared across agents /
  projects. **Track only pods this session created (by name prefix); NEVER delete
  unfamiliar pods or volumes.** Existing paid network volumes are do-not-touch.
  Never point a manifest at another campaign's volume.
- **Self-stop curl must capture `%{http_code}` into a sentinel**, not be silenced
  with `|| true`. A missing / non-2xx code after `pipeline_exit=0` means report
  **`degraded_lifecycle_cleanup`**, not `pipeline_failed`.
- **STOP vs DELETE when a Network Volume is attached:** stopping a volume-attached
  pod does **not** fully clean up (keeps billing/retaining state) — you must
  **DELETE** after verification.

## Adapter implications

Mapped to the provider-adapter contract stages:

- **validate-manifest:** enforce the `cpuFlavorIds` enum
  `{cpu3c,cpu3g,cpu3m,cpu5c,cpu5g,cpu5m}`; warn on `cpu5g`/community-CPU flavors
  (provider-alive/workload-unproven risk) and steer to `cpu5c`+`vcpu4`+Secure;
  warn when `gpuTypeIds: []` (cost/wedge bias) and when an FP8 image is paired
  with an Ampere (SM86) GPU id; require `terminate_after_minutes` present.
- **prepare:** keep the `command -v` preflight-smoke renderer; add slim/conda
  missing-lib detection (`libgomp1`, `xz-utils`, `curl`/`wget`, `free`) keyed off
  the declared image; provision RunPod S3 keys here (not at closeout) for
  read-without-compute egress; for private images require registry-auth + GHCR PAT
  pre-verify + `linux/amd64` child-digest pinning.
- **write-handoff:** record the chosen launch surface (REST `POST /pods`, never
  MCP `create-pod`), the non-default self-stop env var name, the artifact and
  progress ports, and the S3 endpoint/keys reference — all without creating paid
  resources.
- **validate-handoff:** assert the handoff is launchable: payload under the
  conservative ~61KB body cap, gzip (not xz) compression, no inline heredoc in
  `dockerStartCmd`, registry auth present for private images, and a
  `terminate_after_minutes` watchdog set.
- **render-startup:** inject the **`.cycle-complete` short-circuit guard at the
  very top** (complement to the existing `terminal_hold`) because RunPod
  auto-restarts clean exits and there is no `restartPolicy`; default compression
  to **gzip**; forbid inline heredocs; start the artifact HTTP server early;
  auto-set `OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1` for CPU pods.
- **source-checkout:** base64-decode source into the workdir (no heredocs);
  AST-strip docstrings/comments to fit the body cap; on conda/miniforge images use
  `urllib` + Mozilla UA + chunked sha256 fetch (no `curl`/`wget`/`xz`).
- **poll-state:** add a **wedged-host classifier** distinct from crash-loop
  (`uptime` pinned 0-1s with non-trivial elapsed → stop in 10-15 min) and a
  **no-growth detector** (3 zero-growth file-size heartbeats); append `?cb=$RANDOM`
  on all proxy probes; suppress false boot-failure escalation for ~8-10 min when
  `runtime:null` follows a same-machine delete; restart thresholds 1/2/3+.
- **capture-evidence:** byte-sniff fetched artifacts for HTML error pages
  (HTTP 200 ≠ real file) **before** hashing; SHA-256 every durable artifact; treat
  logs as **non-durable on stop** and require a Network-Volume or auth-gated
  forensic channel for crash-prone manifests.
- **egress-plan:** promote **S3-keys-at-prep / read-without-compute** to the
  default durable-summary path; render the **STOP-not-DELETE pull-pod** pattern;
  keep proxy/archive smoke-only.
- **budget-limits:** treat `max_estimated_cost_usd` as advisory only; **require
  `terminate_after_minutes` (= timeout + 15) as the sole hard backstop**; add a
  RAM-ceiling carrier check (carrier-by-RAM, not GPU).
- **billing-report:** compute live cost from wall-clock × `costPerHr`; reconcile
  against `GET /billing/pods`; mark provider numbers as `estimate`; note that
  failed creates cost $0 and warm-image reuse lowers per-shard cost.
- **cleanup-closeout:** enforce post-cleanup prefix `active_count==0`; gate
  shared-account deletes to known prefixes only; capture the self-stop HTTP code →
  `degraded_lifecycle_cleanup` on non-2xx; **DELETE (not STOP)** when a volume is
  attached.
- **supervise / recover-run:** the wedged-host and succeed-loop classifiers are
  the two highest-value recovery triggers; a supervisor that sees uptime pinned
  at 0-1s or ≥3 restart cycles should stop the pod rather than wait.
- **dashboard:** surface, per run, wall-clock spend vs `terminate_after_minutes`,
  restart-cycle count, last artifact-file-size delta, and cleanup
  `active_count` — the four signals that distinguish real progress from
  provider-alive theatre.
- **symphony-outcome:** emit `success` only on hashed-artifact + billing +
  `active_count==0`; emit `degraded_lifecycle_cleanup` (not `pipeline_failed`)
  when the workload finished but self-stop/cleanup was non-2xx.
- **Control-plane note:** any new launch surface must use REST `POST /pods`, not
  MCP `create-pod` (which lacks `dockerStartCmd` / `networkVolumeId` /
  `computeType: CPU`).

## Open questions

- Is there *any* supported way to disable container auto-restart (a
  `restartPolicy`-equivalent) on Pods? Findings say no field exists in REST /
  `PodCreateInput` as of these runs — confirm against the current API before
  relying solely on the sentinel / operator-stop workarounds.
- Exact `containerDiskInGb` caps per `cpuFlavor`×`vcpuCount` tuple are only
  partially observed (`cpu*c`/`cpu*g` ~30-40GB, `cpu*m` ~20GB; 100GB on `cpu3c`
  → HTTP 500 "must be ≤ 40"). The precise matrix is undocumented; record the
  *actual* allocation rather than hard-coding.
- The Secure + `networkVolumeId`-ignores-`cpuFlavorIds`-and-gives-a-GPU anomaly is
  unexplained — frequency/trigger unknown; only mitigation is to verify the actual
  allocation and budget for it.
- The exact POST-body byte ceiling is empirical (~61,440 observed vs ~64KB cited)
  and undocumented — keep the guard conservative.
- The GPU driver→CUDA pinning table reflects 2026-05 observations and **will
  drift** as RunPod updates host drivers; re-verify per campaign.
- Whether RunPod ever surfaces an explicit "wedged host" / "image pull failed"
  signal through any API field (vs. inferring it from pinned-zero uptime) is
  unknown — current practice is inference + a hard stop timer.
