# RunPod Status Taxonomy

Use the exact machine-readable fields in automation. Human labels are summaries only.

## Machine Fields

| Field | Owner | Meaning |
| --- | --- | --- |
| `remote_run_record.status` | bridge remote runner | End-to-end remote run state across create, verification, artifact proof, and cleanup. |
| `cleanup.status` | cleanup flow | Cleanup request and verification state for one pod. |
| `progress_report.classification.state` | progress classifier | Live progress classification from provider runtime and progress endpoint probes. |
| `monitor.state` | local packet monitor | Local workload packet state; not final remote success. |
| `closeout.status` | artifact closeout | Artifact/status/evidence validation result for a fetched packet. |

## Remote Run Statuses

| `remote_run_record.status` | Interpretation | Success? | Next action |
| --- | --- | --- | --- |
| `dry_run_request` | Request rendered only; no paid pod created. | No | Review plan and launch gates. |
| `blocked_launch_lock` | Another owner already holds the launch lock. | No | Inspect lock owner and active pods. |
| `blocked_*`, `failed_create_request`, `created_missing_pod_id` | Create did not produce a usable pod record. | No | List pods by prefix before retrying. |
| `created_unverified` | Pod was created but artifact verification was skipped. | No | Verify artifacts or clean up. |
| `verification_error` | Verifier crashed before proving artifacts. | No | Preserve verifier error and clean up. |
| `verification_failed` | Verifier ran but required artifact/status proof failed. | No | Fetch failure packet, inspect missing evidence, then clean up. |
| `artifacts_verified_cleanup_pending` | Required artifacts passed, cleanup has not yet been proven. | No | Verify cleanup. |
| `cleanup_unverified` | Cleanup was submitted but stopped/deleted/absent state was not proven. | No | Run `cleanup-pod --wait` or verify absence. |
| `cleanup_failed` | Cleanup request or verification failed. | No | Escalate cleanup before reporting success. |
| `succeeded` | Artifacts validated and cleanup is `verified` or `already_absent`. | Yes | Write outcome and cost closeout. |

## Cleanup Statuses

| `cleanup.status` | Meaning | Shell success? |
| --- | --- | --- |
| `dry_run_request` | Cleanup command was rendered only. | Yes for dry run only. |
| `submitted` | Stop/delete request was sent but not verified. | No; CLI exits `2`. |
| `verified` | Stop/delete target state was verified. | Yes. |
| `already_absent` | Pod was already absent. | Yes. |
| `timeout` or `failed` | Cleanup could not be proven. | No. |

## Progress Classification

| `progress_report.classification.state` | Meaning |
| --- | --- |
| `workload_progressing` | A repeated sample proved workload evidence advanced, such as log bytes, hash ledger bytes, or status payload. |
| `harness_alive_progress_unproven` | Progress endpoint responded, but only proves monitor liveness. |
| `harness_progress_workload_unproven` | Harness phase changed, but workload evidence did not. |
| `provider_alive_workload_unproven` | Provider runtime/control plane responded, but workload progress is unknown. |
| `terminal_reported` | Workload reported terminal status; fetch artifacts, validate hashes, then clean up. |
| `terminal_failed` | Workload reported failure; fetch failure packet, then clean up. |
| `pod_unhealthy_or_unobservable` | Runtime telemetry suggests crash loop or invalid/missing runtime proof. |
| `unknown_no_reliable_progress_signal` | No reliable signal; retry independent probes before calling outage. |

## Human Labels

Use human labels only in summaries. Map them to machine fields in the outcome:

| Human label | Required machine evidence |
| --- | --- |
| `blocked_provider_create` | `remote_run_record.status` is create-blocked or create-failed and no pod ID exists. |
| `blocked_provider_runtime` | Pod exists, but runtime/progress never proves container truth. |
| `crash_loop_suspected` | `progress_report.classification.state=pod_unhealthy_or_unobservable` or runtime uptime resets. |
| `blocked_bootstrap` | Fetched status/logs show bootstrap failure before workload commands. |
| `blocked_observability` | Pod may be running but no progress/artifact channel proves workload state. |
| `succeeded_artifacts_verified_cleanup_done` | `remote_run_record.status=succeeded`, artifact hashes present, and cleanup verified. |
