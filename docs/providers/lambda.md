# Lambda Cloud Provider Reference

> Lambda Cloud (Lambda Labs GPU VMs) — **NOT AWS Lambda**, NOT a container-as-pod
> platform like RunPod. A provider setup target for the multi-provider bridge.

## Overview / positioning

Lambda Cloud is a **raw GPU-VM** provider. The unit of compute is a full Ubuntu VM
with the Lambda Stack pre-installed (CUDA, PyTorch, Docker, NVIDIA Container Toolkit,
JupyterLab). The operational loop is: launch one short-lived instance over REST, wait
for SSH, push a minimal source bundle, run the workload, pull a **hashed artifact
archive** back over SCP, then **terminate and verify absence**.

Positioned strictly as a **GPU-VM fallback** for when RunPod is blocked by capacity,
registry auth, or pod networking — not a drop-in replacement. Run the **same manifest /
stage-contract / input-audit / self-check** on the VM that you would for RunPod. Execution
has setup guidance only in this bridge; start with a single-GPU public smoke before
treating the provider notes as a launch guide.

> **DANGER FLAG.** Lambda has **hourly (not per-second) billing** and **no local hard
> spend cap** in the launcher. Instances bill from boot until you **explicitly
> terminate** them — there is no stop/hibernate, only terminate. Treat Lambda as **more
> dangerous than the RunPod bridge**: a forgotten instance is an unbounded bill, and a
> leftover persistent filesystem keeps billing **independently**. The only safety is
> immediate terminate + post-terminate verification of **both** instances and
> filesystems.

**Cross-provider RULE (applies here too):** provider RUNNING / `status==active` /
exit-0 / app-logs / a live SSH connection / a tailing pipe are **NEVER** workload
success. Closeout requires **fetched + hashed artifacts**, a billing tally, and
**verified cleanup**.

## API surface & authentication

- **Base URL:** `https://cloud.lambda.ai/api/v1` (older docs/code show
  `cloud.lambdalabs.com/api/v1`; both observed — pin the `.ai` host). API docs:
  `https://docs-api.lambda.ai/api/cloud`.
- **Auth:** API key as HTTP basic-auth **username with a blank password**
  (`curl -u $LAMBDA_API_KEY:`).
- **CRITICAL Cloudflare gotcha:** the API sits behind Cloudflare, which returns an
  **HTML 403** to default `urllib` / `requests` / browser user-agents. You **must shell
  out to `curl`**. The planned wrapper drives `curl --config -` on **stdin** so the key is
  never printed, written to disk, or placed in `argv`. Config lines: `request`, `url`,
  `header = "accept: application/json"`, `user = "<key>:"`, and for bodies
  `header = "content-type: application/json"` + `data = <double-json-encoded payload>`.
- **Key storage:** OS keychain or an env var (`LAMBDA_API_KEY`). Never store the key in
  repo files, `.env`, markdown, or command lines.

### Endpoints used

| Method + path | Purpose |
|---|---|
| `GET /instances` | List live instances (poll + post-terminate verification) |
| `GET /file-systems` | List persistent filesystems (independent billing check) |
| `GET /instance-types` | Capacity probe via `regions_with_capacity_available`; carries `price_cents_per_hour` |
| `POST /instance-operations/launch` | Launch; returns `data.instance_ids` (expect **exactly one**) |
| `POST /instance-operations/terminate` | Teardown: `{"instance_ids": [...]}` |

**Idempotency rule (money-safety):** GET calls (instance/capacity polls) **retry**
transient curl/network failures (e.g. `curl (35) Recv failure`) with backoff.
**Non-GET (launch/terminate) does NOT auto-retry** — it raises on the first failure,
because a double-launch burns money.

## Launch & command rendering

Launch body fields: `region_name`, `instance_type_name`, `ssh_key_names: [...]`,
optional `file_system_names: [...]` / `file_system_mounts`, optional `firewall_rulesets`,
`name`, `quantity` / `user_data`.

Pre-flight discipline:

1. **Capacity probe first.** `GET /instance-types`; confirm the target region is in
   `regions_with_capacity_available`. Capacity is **region- and type-specific and
   volatile** — cheap GPUs (and high-end H100/H200 during US business hours) open/close
   in windows; launch returns non-200 when unavailable. Poll multiple regions or accept
   `_pcie` when `_sxm` is gone.
2. **Refuse to launch if instances already exist** (raise
   `refusing to launch while Lambda instances already exist`). A stray pre-existing
   instance was once found still booting/billing.
3. **Exactly one instance, no persistent filesystem** for smoke/canary, short wall
   clock, immediate terminate.
4. **Instance NAMEs must be ≤ 64 chars** (Lambda rejects longer). Use short, run-scoped
   names.

### SSH keys

The SSH key must be **pre-registered** in Lambda (console or API); there is no inline
keypair creation at launch. Two patterns:

- **Registered key already on the host:** reference it by `ssh_key_names`.
- **Ephemeral per-run key** (register the `.pub` only, private key stays off the
  launching machine): generate a throwaway keypair, register its pubkey via the API, use
  it for the run, then **DELETE the Lambda key entry at teardown** so the account stays
  clean.

### Image / instance-type pinning

**Image IDs are misleading — never trust the name, always probe.** A requested
"Lambda Stack 24.04" A10 image booted **Ubuntu 22.04 with no python3.12**. Pin a
known-good `image_id` per instance type. Carry a `PROFILES`-style table with
`instance_type`, `region`, **pinned `image_id`**, `price_usd_per_hour`, and per-type
chunking/timeout knobs, making cost/capacity explicit and selectable. Instance-type
enums are descriptive: `gpu_1x_a10`, `gpu_1x_a100`, `gpu_1x_a100_sxm4`, `gpu_1x_gh200`,
`gpu_1x_h100` / `_pcie` / `_sxm5`, `gpu_8x_h100_sxm5`, `gpu_1x_a6000`,
`gpu_1x_quadro_rtx_6000`.

**Lambda is GPU-ONLY and FIXED-PRICE:** no spot/preemptible market, no offer search,
no bidding, no CPU-only instances. For CPU jobs, stay on RunPod — Lambda is the wrong
tool.

### Lifecycle loop (every step appended to a JSONL ledger)

There is **no native onstart/cloud-init as reliable as a hyperscaler's**. The proven
primary path is **SSH-after-IP-poll**, with an explicit operator fallback ("SSH manually
if the IP hasn't appeared after ~10 min").

1. **Capacity check** → **launch** (expect exactly one `instance_id`).
2. **Poll** `GET /instances` (~20s interval) until `status == "active"` AND
   `ip` / `hostname` present. Boot is **async + slow**: single-GPU often 1–3 min, one run
   saw **~7 min**. Use a generous `max_boot_seconds`.
3. **`wait_ssh`** — SSH probe loop (`-o StrictHostKeyChecking=accept-new`, per-run
   `UserKnownHostsFile`, `ConnectTimeout=10`, `BatchMode=yes`), retry ~15s until
   `ssh ... true` returns 0.
4. **`scp` the minimal source bundle** (tar.gz of only declared source files).
5. **Run remote bash** as `ubuntu@<ip>`, workspace `/home/ubuntu`. The remote script
   should `exec > >(tee "$RUN_ROOT/remote-full.log") 2>&1` and **probe the environment as
   a hard gate**: `cat /etc/os-release`, `nvidia-smi`, `python3.12 --version`, `df -h /`.
   Treat OS/Python **drift as failure**.
6. **`scp` the result archive + remote `.sha256`** back, **compare local vs remote sha**,
   raise on mismatch.
7. **`safe_extract`** locally (see Durable artifact egress).
8. **Terminate**, then **`wait_cleanup`** polls `GET /instances` until the id/name
   disappears (~77s observed) and re-checks `GET /file-systems`.

### Runtime bootstrap inside the VM

- **Managed Python via `uv`:** if `python3.12` is missing,
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, `uv python install 3.12`,
  `uv venv --python "$PYBIN"`. **Trap:** the resulting `uv venv` may **not include
  pip** — install with `uv pip install --python "$VENV_PY"` when pip is absent (keep an
  `install_pkg` shim that falls back from `pip` to `uv pip`).
- **CUDA/torch clobber trap (re-pin twice):** a source install can silently upgrade
  torch to a `cu130` build, leaving `torch.cuda.is_available() == False` on Lambda's
  CUDA 12.8 driver even though `nvidia-smi` works. Pin `torch==2.7.0+cu128` from
  `https://download.pytorch.org/whl/cu128` **before the run AND AGAIN after any source
  install that touches torch**. Probe `torch.cuda.is_available()` and `SystemExit(1)` on
  failure. (A working pin: arm64 `lambda-stack-24-04` image in `us-east-3` gave Ubuntu
  24.04 + Python 3.12.3 + torch 2.7.0 + CUDA 12.8 + GH200 visibility.)
- **conda ToS gate:** `conda env create` blocks on unaccepted channel ToS — run
  `conda tos accept --override-channels --channel <main>` (and `<r>`) **before** any env
  create, or the build fails **after** the VM is already billing.
- Provider-agnostic boot contract is identical to RunPod: tool-name / run-id /
  mount-path / workdir env + `STATUS` / `SUCCESS` / `FAILURE` sentinel files. The only
  deltas vs RunPod are egress (SSH `cat <MOUNT>/<tool>/<run>/STATUS` instead of proxy
  HTTP) and teardown (REST terminate).

For a Docker-based workload, either pass `user_data` (cloud-init) that runs `docker run`,
or SSH in and run it. Optional staging modes: **S3** (`aws s3 cp` + `aws s3 presign
--expires-in 43200` to hand the VM a 12h presigned `boot.sh` URL) or a manual external
URL base.

## Monitoring / observability (and what is NON-authoritative)

Instance `status == "active"`, an SSH connection, or a tailing pipe are **NOT
authoritative success signals**. Authoritative success comes only from the **fetched,
hashed artifact archive** plus the workload-written status file.

Live progress monitoring (keyed on **changing state**, since stdout buffers):

- **Liveness:** cumulative CPU-seconds **delta** per cycle of the tracked PID —
  `ps -o times=` on the nohup'd PID. A stall reads `+0`. **Piping a build through `tail`
  is a DEAD progress signal once stdout is buffered.**
- **Progress depth:** count output files / sub-directories over time.
- **GPU fit:** compare GPU utilization against workload phase. A CPU-bound stage on a GPU
  instance is mostly wasted hourly spend. Match the card to the stage.

**Monitoring bugs that cause idle billing (avoid these):**

- `pgrep -f run.sh` **self-matches the monitor's own shell** → always reads ALIVE. Track
  the nohup'd PID and use `kill -0 $PID`.
- A watch loop that breaks only on success/process-death **misses a Traceback in its own
  captured output** → also break+alert on error signatures.
- An error `grep` over an appended log **keeps tripping on stale errors** → scope the
  grep to **after the last run-start marker**.

## Durable artifact egress

Lambda has **no managed artifact proxy and no object store**. Durable egress is
**SSH/SCP + SHA-256 round-trip verification** (the Lambda analog of S3-checksum egress):

- Remote writes `tar.gz` + `.sha256` (e.g.
  `sha256sum "/tmp/$RUN_ID.tar.gz" > "/tmp/$RUN_ID.tar.gz.sha256"`).
- Host pulls **both** with retry, recomputes the local hash, and verifies **equality
  before trusting the archive** (raise on mismatch).
- Keep raw data and heavy outputs on the VM disk / attached NFS, **never in git**; copy
  back **only small declared artifacts**.

**Archive/extract traps:**

- Put **all GNU tar `--exclude` options BEFORE the source path** and exclude the venv +
  weight files aggressively (`venv`, `*.safetensors`, `*.bin`, `*.pt`, `*.pth`,
  `.cache`). Archive **only declared artifact dirs**.
- Python `tarfile.extractall` **fails closed on absolute symlinks** inside virtualenvs —
  a successful run was once marked failed only because local extraction refused an
  absolute venv symlink **after** artifacts were already fetched. Use a `safe_extract`
  wrapper that rejects members with leading `/` or `..` and never archive the venv.

### Persistent filesystem (Lambda's killer feature — and its planning constraint)

NFS-backed, **free at small scale** (2026-05), auto-mounted at
`/lambda/nfs/<filesystem-name>`, up to 8 EB/filesystem, 24 filesystems/account. Great
for "stage a 100 GB reference DB once, mount free into every GPU run" (vs a RunPod volume
~$70/mo for 1 TB). Constraints:

- **Attach at LAUNCH time only** (pass `file_system_names`); you **cannot attach to a
  running instance**.
- **Region-locked:** instance and FS must share a region; you cannot move a populated FS
  between regions. **Pick the region by where your populated filesystem already lives.**
- Without NFS, every instance is fresh local SSD (no durable local disk) — pre-stage
  everything to NFS or S3 + on-boot fetch.
- Uplinks ~10–25 Gbps, **no egress fees**. SOC2 Type II + ISO27001, HIPAA available.

## Cost & budget control

- **Hourly, not per-second.** Billing starts **after boot + health-check passes** —
  factor multi-minute boot into cost, not just compute time. The bridge's RunPod budget
  reasoning ("seconds of wall clock × costPerHr") must switch to **"hours of instance
  uptime"** for Lambda. Estimate: `cost = round(elapsed / 3600 * price, 4)`, with `price`
  refreshed from the live `instance_type.price_cents_per_hour / 100` after launch.
- **No local hard spend cap** — discipline is the only control.
- **Count failed attempts and idle/monitoring waste in the tally**, not just the
  successful run.

### Flat on-demand pricing examples (2026-05)

| Instance | GPU | $/hr |
|---|---|---|
| `gpu_1x_quadro_rtx_6000` | 24 GB | 0.69 |
| `gpu_1x_a10` | 24 GB | 0.75 list price; verify current billing before relying on it |
| `gpu_1x_a6000` | 48 GB | 1.09 |
| `gpu_1x_a100` | 40 GB | 1.10–1.99 |
| `gpu_1x_a100_sxm4` | 40 GB SXM4 | 1.99 |
| `gpu_1x_gh200` | — | ~2.29 |
| `gpu_1x_a100_sxm` | 80 GB | 2.79 |
| `gpu_1x_h100` | 80 GB | 2.49–3.29 |
| `gpu_8x_h100` | — | ~19.88–32 |

No spot. NFS effectively free at small scale. Simplest pricing of any GPU cloud, but
cheapest H100 ≈ 2× a spot-market neocloud.

### Cost discipline checklist

- Start with one short single-GPU smoke and no persistent filesystem.
- Set a low explicit spend ceiling and count failed attempts plus idle monitoring time.
- Terminate immediately after artifact pullback and cleanup verification.
- Treat CPU-bound phases on GPU instances as a route-planning error.

## Cleanup / closeout

- **Terminate is the only teardown** — no stop/hibernate; every instance is destroyed.
  `POST /instance-operations/terminate {"instance_ids": [...]}`. Non-GET → no auto-retry.
- **Two independent verification checks at closeout:** `GET /instances` must show the
  run's id/name **gone** (`wait_cleanup` until absent), AND `GET /file-systems` must
  show **no residual filesystem** (filesystems bill separately and survive instance
  termination). Closeout records "no residual instances or filesystems" as **two separate
  confirmations**.
- If an ephemeral SSH key was registered for the run, **delete the Lambda key entry** so
  the account is clean.
- Non-GET teardown failure raises immediately; log a `cleanup_error` event but never
  silently assume cleanup succeeded.

### Parseable outcome predicate

The wrapper's outcome predicate (Lambda analog of `symphony-outcome`) is conjunctive over
**artifact + closeout + status**:

```
ok = (error is None) and cleanup_verified and archive.exists() and (prediction_count > 0)
```

The outcome record also carries `price_usd_per_hour`, `cost_est_usd`,
`cleanup_requested`, `cleanup_verified`, `archive_sha256`, and per-artifact
`{path, bytes, sha256}` rows. A provider-neutral compute profile exists precisely so a
non-RunPod GPU host can be swapped in **as long as it preserves the identical artifact +
closeout + outcome contract**.

## Adapter implications

Mapping the findings to the portable provider-adapter contract stages:

- **validate-manifest / prepare:** Same manifest, stage-contract, and input-audit as
  RunPod (Lambda is a fallback, not a different contract). Profile must declare
  `instance_type`, `region`, **pinned `image_id`**, `price_usd_per_hour`, and (if used)
  region-locked `file_system_names`. Set `provider.adapter = lambda_cloud_vm_v1`. Validation
  must **reject CPU-only workloads** (Lambda is GPU-only) and **reject FS-attach to a
  running instance**.
- **write-handoff:** Record the worker→orchestrator boundary without paid resource
  creation, same as RunPod. Capture: API-key source (keychain/env, never in repo),
  `ssh_key_names`, target region+type, capacity-probe result, ephemeral-key plan, and
  durable-egress plan.
- **validate-handoff:** Confirm the handoff is complete and an **operator gate is
  satisfied before any launch** (launch spends money with no platform cap).
- **render-startup:** No reliable cloud-init; render an **SSH-after-IP-poll** plan with
  an explicit "SSH manually after ~10 min" fallback. Remote script must `tee` a full log,
  **hard-gate on os-release / nvidia-smi / python probes**, install via `uv` with the
  pip-absent fallback, and **re-pin `torch==2.7.0+cu128` after any source install**. Emit
  the same tool-name/run-id/mount/workdir env + `STATUS` / `SUCCESS` / `FAILURE`
  sentinels.
- **source-checkout:** Minimal `scp` source bundle (declared files only) or pinned
  `git clone`. Persistent NFS mounts at `/lambda/nfs/<name>`, **launch-time-only attach,
  region-locked**. No durable local disk otherwise.
- **poll-state:** `GET /instances` until `status==active` AND ip/hostname present;
  generous boot timeout (1–7 min). GETs retry transient curl failures; launch/terminate
  never auto-retry.
- **capture-evidence:** No managed proxy/exec. Liveness via CPU-seconds delta on the
  nohup'd PID (`kill -0 $PID`, **not** `pgrep -f`); progress via output-file counts.
  `tail` / buffered stdout and `status==active` are **explicitly non-authoritative**.
  Scope error-greps to after the last run-start marker. Required SHA-256 reporting:
  remote `sha256sum` → `.sha256`, host recomputes and asserts equality, per-file
  `{path, bytes, sha256}` rows — satisfied **without** an object store.
- **egress-plan:** SSH/SCP + SHA-256 round-trip is the durable mechanism (no S3 by
  default; optional S3 staging for `boot.sh`). Proof = matching local/remote hash before
  trust. `safe_extract` (reject `/` and `..` members), exclude venv + weights, tar
  `--exclude` **before** the source path.
- **budget-limits:** Reason in **hours of uptime**, not seconds. **No platform-side
  cap** — the adapter must enforce locally via `terminate_after`-style wall-clock guards,
  refuse-if-instances-exist, single-instance discipline, and `max_boot` / `max_ssh` /
  `max_run` timeouts.
- **billing-report:** No documented `/billing` REST endpoint. Cost = `elapsed_hours ×
  price`, price refreshed from `instance_type.price_cents_per_hour`. **Boot time and
  failed attempts count.** No spot.
- **cleanup-closeout:** Terminate-only; **two-check verification** (`GET /instances`
  gone AND `GET /file-systems` empty); delete any ephemeral SSH key. The RunPod
  single-resource cleanup model must be extended to **N resource classes** (instances +
  filesystems + keys).
- **supervise:** Drive the lifecycle loop off the JSONL ledger; break+alert on error
  signatures (not only on success/process-death), and guard against the
  pgrep-self-match / stale-grep / buffered-tail idle-billing traps.
- **dashboard:** Surface live instance `status`, IP, CPU-seconds delta, output-file
  count, running cost estimate (`hours × price`), and an explicit
  instances-and-filesystems residual indicator.
- **symphony-outcome:** `ok = error is None AND cleanup_verified AND archive.exists()
  AND prediction_count > 0`, plus `price` / `cost_est` / `cleanup_verified` /
  `archive_sha256` + per-file rows. The provider-neutral profile preserves the identical
  outcome shape so a non-RunPod GPU host is swappable.

## Open questions

- **Canonical API host:** `cloud.lambda.ai` vs `cloud.lambdalabs.com` — both appear in
  code/docs. Confirm the stable host and whether `lambdalabs.com` is a redirect.
- **Firewall rulesets & user_data/cloud-init:** the launch body accepts
  `firewall_rulesets` and `user_data`, but this bridge has not validated them end-to-end.
  Use SSH-after-poll until a public smoke proves a true onstart path.
- **Billing API:** is there a programmatic billing/usage endpoint (RunPod has
  `/billing/*`)? Treat cost numbers as **estimated** from `price × hours`, not pulled
  from a provider invoice surface.
- **Filesystem lifecycle API:** create/delete/list of persistent filesystems was not
  exercised (only `GET /file-systems` for verification). Create/attach/region-move
  semantics need a documented API mapping before relying on NFS in the adapter.
- **Quota & launch-failure taxonomy:** launch returns non-200 for
  capacity / SSH-key-not-registered / billing-not-configured, but the exact error
  codes/bodies to branch on are not captured — surface them to the operator and build the
  taxonomy from controlled validation launches.
- **Multi-GPU / 8× topology:** start validation with single-GPU (`gpu_1x_*`).
  `gpu_8x_h100_sxm5` rank / world-size / rendezvous and multi-node aggregation are
  untested on Lambda.
- **Per-second billing:** confirm before launch and re-check regularly, as
  providers change billing granularity.
