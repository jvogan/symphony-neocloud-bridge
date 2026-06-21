# RunPod Symphony Contract Checklist

Use this checklist when validating a Linear issue or launch manifest without a dedicated `cloud-bridge` CLI.

## Required Manifest Fields

- `schema_version` and `manifest_kind`
- `run_id` and `compute_profile`
- `remote_launch_allowed`
- `launch_authorization.source`, `launch_authorization.approved_by`, and `launch_authorization.approved_at` when remote launch is allowed
- `budget.max_runtime_minutes`
- `budget.max_estimated_cost_usd`
- `repo.source`, `repo.url_or_path`, `repo.commit_or_snapshot`, and `repo.workdir`
- `runpod.imageName` or an approved template reference
- `access` booleans for SSH, full SSH/SCP, HTTP proxy, TCP ports, and public service auth policy
- `startup.mode` and `startup.commands`
- `startup.log_file`, `startup.status_file`, and `startup.heartbeat_file`
- `monitoring.poll_interval_seconds` and `monitoring.max_silent_minutes`
- `validation_commands`
- `expected_artifacts`
- `artifact_egress.mode`
- `worker_coordination.linear_issue_lock_required`
- `worker_coordination.single_mutating_worker`
- `closeout.stop_or_delete_pod`
- `closeout.linear_outcome_required`
- `safety.no_literal_secrets`

## Stage Contract Self-Check

Every paid launch must prove the route that will actually run, not only that flags were accepted or packages are installed.

Require `workload.stage_contract` with:

- `inputs`: concrete ledgers, manifests, datasets, or files the stage will read.
- `exact_commands`: the workload commands and artifact validation commands that will run, not only `which`, `--version`, `pip show`, or package-list checks.
- `route_proof.input_materialization`: proof that the real input ledger, manifest, target, index, or dataset materialized before execution.
- `route_proof.tool_invocation`: proof that the workload route invoked the exact command or tool used for the deliverable.
- `route_proof.artifact_validation`: proof that validation inspects declared artifact paths or the artifact directory.
- `route_proof.claim_boundaries`: proof that closeout language cannot overstate what the artifacts establish.
- `required_tools`: for large or huge runs, fail-closed exact executable checks for the commands that paid stages will call.
- `expected_outputs`: real artifact paths that match `expected_artifacts`.
- `done_markers`: files that prove the workload reached its declared terminal state.
- `timeout_minutes`: finite stage budget.
- `resume_policy`: rerun or checkpoint behavior.
- `fail_closed: true`: failed validation cannot close out as success.
- `claim_level`: one of `artifact_execution_only`, `unsupported`, `observed`, `inferred`, `candidate`, or `validated`.
- `fallback_policy`: for large or huge runs, `no_silent_fallback: true` and `degraded_closeout_required: true`.
- `partial_summary_path`: for large or huge runs, an artifact written on failure, timeout, interruption, or degraded closeout.
- `cardinality_gate`: for large or huge runs, estimate, budget limit, and behavior when fanout exceeds the limit.
- `normalized_outputs`: stable ledgers or summaries derived from raw tool output.
- `stale_marker_policy`: for checkpointed runs, input-hash requirements before prior done markers can be trusted.

Fail closed when live expected artifacts, stage outputs, or scanned text artifact content contain names such as `mock`, `fake`, `dummy`, `provider_search`, or `target_species_placeholder`.

Before asking the user for missing inputs, workers should read the issue body, launch manifest, repo ledgers, and existing run records. If the accession, file, target, constraint, or artifact path is already present, summarize it and continue.

For analytical or scientific workloads, separate primary evidence, context evidence, and dossier material. Reference hits, provider search results, controls, and background annotations can support a report, but they do not satisfy a target deliverable unless the contract explicitly says they are the deliverable.

For fanout workloads, run a cardinality gate before paid launch. The gate should estimate shard count, target count, output size, runtime, and budget behavior. Annotate once and join many when possible instead of repeating expensive steps per downstream report.

Fallbacks must be explicit. A live-to-mock, provider-to-local, full-to-rescue, private-source-to-reference, or wide-to-narrow fallback cannot close as success unless the declared original artifacts still validate. Otherwise close degraded or partial with a partial summary.

## Launch Gate

Remote launch is blocked unless all are true:

- `remote_launch_allowed: true`
- `launch_authorization` records the operator or Linear source that explicitly authorizes launch.
- Budget and max runtime are finite and acceptable.
- Cleanup policy is explicit.
- Expected artifacts are declared with paths.
- Monitoring and artifact egress are declared.
- `contract-self-check` has no errors.
- `preflight` and `egress-plan` have no errors for large or huge workloads.
- Validation commands are declared.
- Exposed ports match the access policy, for example `8000/http` for HTTP proxy inspection or `22/tcp` for full SSH/SCP.
- Repo source is exact enough to reproduce.
- Remote git launches use an immutable commit SHA or snapshot/archive digest, not a moving branch.
- Private registry images have provider-side RunPod registry auth or an exact image-pull canary recorded as `runpod.image_pull_verified: true`.
- Linear issue lock and single mutating worker policy are declared.
- A local preflight packet has been prepared and reviewed.
- `provider_handoff.json` has been written and validated before any orchestrator-side paid launch.
- The exact RunPod create request has been reviewed before `--execute`.
- Manifest and Linear text do not contain literal secrets or private data.
- A tiny real smoke has already exercised the same route, provider, artifact retrieval, and closeout path before large or huge launch.
- Long, expensive, large, or huge runs have a live productivity channel: sanitized `/healthz`, SSH/log tail, or another fetchable status/heartbeat packet. Provider state, runtime metrics, billing, monitor heartbeat, and completion-only artifact inspection do not satisfy this gate.
- The monitoring contract defines advancement criteria. Workload progress requires repeated samples with log growth, status/progress counter changes, artifact/hash ledger growth, or SSH tail output.
- Long batch runs that need post-completion inspection should set `startup.terminal_hold.mode: sleep_infinity` or another explicit terminal hold and rely on orchestrator cleanup after artifact proof.

## Secret And Data Screening

Reject manifests, issue text, and logs that contain literal values for:

- API keys, tokens, passwords, private keys, or registry credentials
- Raw private datasets or customer process records
- Unpublished sequences or proprietary process parameters
- Inline `.env` contents

Prefer secure-store names, environment variable names, or runtime injection references instead of literal values.

## Closeout Requirements

Every closeout must include:

- pod ID and template or image
- compute profile, data center, runtime, and cost estimate
- cost source, preferably `billing_api` when `cost-report --fetch-billing` succeeds
- validation command results
- artifact paths and SHA-256 hashes
- forbidden artifact marker scan results for text artifacts
- egress status for workspace archive, network volume, SCP, or object-store upload
- cleanup action and status
- retained resource ID and approver, if retention was approved
- monitoring summary with pod state, workload status, last heartbeat, and silence-timeout result
- parseable `symphony-outcome` block

Treat provider state as intent, not truth. `RUNNING`, pod start, worker exit, and command return code are not enough without workload status, logs, hashes, and declared artifacts.

Set the claim level to artifact execution only unless the domain repo separately validates scientific or analytical claims.
