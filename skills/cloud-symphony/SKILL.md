---
name: cloud-symphony
description: "Use cloud compute for AI-agent research and engineering: select providers, plan and execute supported workloads, validate artifacts between tools, and track cost and cleanup."
---

# Neocloud Bridge

Use this skill for repository-defined workloads that may run on cloud or managed compute. Keep workload logic and domain validation in the domain repository. Use this bridge for the portable manifest, provider lifecycle, evidence, budget, and cleanup contracts.

For multi-stage work, use the [agent workflow guide](../../docs/agent-workflows.md) and the [runnable count-table example](../../examples/research-table-qc/). Keep dependencies between runs in the agent or workflow engine.

## Provider choice and support

Preserve the provider the user selected. Do not propose, substitute, or route through RunPod, Hugging Face, or another provider merely because this bridge has a bundled runner for it. A provider change requires the user's explicit approval.

The installed bridge currently bundles guarded lifecycle commands for:

- RunPod Pods: create, observe, artifact verification, billing evidence, and cleanup.
- Hugging Face Jobs: submit, poll, log capture, artifact verification, duplicate check, and cancel-on-abort.

A registry label of `setup guidance` describes only the bundled commands in this bridge version. It is not a ban on the provider, does not invalidate explicit provider-specific authorization, and does not block using a project-owned adapter, a provider-native SDK or CLI, or implementing the missing guarded adapter when that is part of the task.

For a selected provider without a bundled runner, inspect the scoped project and local tooling for an existing execution path. If the task includes adapter work, complete and validate that adapter against the portable evidence, budget, retry, and closeout contract. Treat a missing bundled command as implementation status, not as an authorization blocker. Stop only when the requested paid execution still lacks a real executable path or a genuine launch gate cannot be satisfied after the authorized in-scope work is complete. When provider authorization is already explicit, describe that outcome precisely as a capability or launch-gate blocker rather than claiming authorization is missing.

The RunPod runner still uses legacy REST API v1. RunPod documents v1 retirement on November 15, 2026; see the [migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1). Current `runpodctl` releases no longer provide the former stop/terminate creation flags. `budget.terminate_after_minutes` is an orchestrator deadline, not a provider-enforced TTL.

## Safety rules

- Start with local validation and dry-run planning.
- Never create a paid resource or submit a paid request without explicit user authorization for that provider and run, launch-authorization evidence, and finite cost and execution bounds. Use `remote_launch_allowed: true` when the selected manifest/runner defines that field; do not invent incompatible RunPod-shaped fields for another provider.
- One explicit current-task authorization can cover the named provider, run, and stated cost scope, including ordinary retries within that scope. Do not ask for a duplicate confirmation unless the provider, external effect, data exposure, or cost scope changes. A plan-only request is not launch authorization.
- Put secret references in manifests. Never store credentials, account identifiers, private data, presigned URLs, or private source paths in repository files, issue text, logs, or artifacts.
- Use one mutating owner per run. Keep launch locks and duplicate checks enabled.
- Treat provider readiness, command exit, and log presence as evidence, not success.
- Require declared artifacts, validation, SHA-256 hashes, cost evidence, and verified cleanup or approved retention before reporting success.
- Keep public endpoints limited to sanitized data or protect them with application-layer authentication.
- Do not route one provider through another provider's runner or represent provider-native execution as a bundled bridge capability.

## Inputs to establish

- provider and selected execution path: bundled registered adapter, project-owned adapter, or guarded provider-native wrapper;
- source of remote-launch authorization;
- immutable input ledger, reproducible source reference, or bounded inline workload, as appropriate to the selected provider surface;
- exact workload and validation commands;
- required artifacts and claim boundary;
- provider-appropriate resource or request shape, runtime or supervision bound, retry/concurrency limits, and spend ceiling;
- monitoring and silence-timeout policy;
- artifact-egress and cleanup ownership;
- runtime secret references and data policy.

## Default workflow

1. Establish the chosen provider and actual execution surface: bundled bridge runner, project-owned adapter, or provider-native SDK/CLI.
2. Validate the workload with the commands defined by that surface. Use `validate-manifest`, `contract-self-check`, `preflight`, `egress-plan`, and `plan` when the bridge manifest supports the selected adapter; otherwise apply the same contract through the project's provider-specific validation instead of forcing an incompatible manifest.
3. Prove inputs, exact requests or commands, outputs, done markers, validation depth, retry/resume behavior, duplicate suppression, and claim level without provider mutation.
4. If the selected execution surface is incomplete and adapter implementation is in scope, finish it and its tests. Do not stop merely because the pre-edit registry still says `setup guidance`.
5. For RunPod, use `prepare` and `validate-handoff` when an orchestrator will own the launch. These commands are RunPod-specific.
6. For RunPod Pods, prefer `run-remote` or `run-handoff`. For Hugging Face Jobs, use `run-job`.
7. For any other provider, use only its validated project-owned or provider-native path; never pass its workload to a RunPod or Hugging Face command.
8. Stop at dry-run unless the selected path's genuine launch gates pass. If they pass and the user has authorized execution, proceed rather than treating the absence of a bundled bridge command as a new blocker.
9. Observe provider state and workload evidence separately. Match monitoring to the provider lifecycle instead of requiring VM or pod telemetry from request-based inference.
10. Retrieve and validate every required artifact, then record hashes.
11. Capture cost evidence and verify request cancellation, resource deletion, job termination, or approved retention as applicable.
12. Render the final `symphony-outcome` from the audited record or an equivalent parseable provider-specific receipt.

## Provider notes

### RunPod

- Use Secure Cloud unless a sanitized public smoke explicitly permits Community Cloud.
- Check exact image prerequisites before launch; startup commands run after source bootstrap.
- Keep Pod state, runtime telemetry, workload progress, and artifact proof as separate facts.
- API v2 log streaming is an upstream capability, not a current bridge command.
- `render-runpodctl-create` is command review only and omits the lifecycle flags removed in `runpodctl` v2.12.0.
- Delete Pods at closeout unless retention is explicitly approved. Account separately for retained volumes.

### Hugging Face Jobs

- Pass `--max-spend-usd` to bound the job estimate and check the selected hardware price before launch.
- Set `huggingface.timeoutSeconds` within the authorized runtime and include it in the cost estimate.
- Secret values are resolved only at execution time and sent through the Jobs secret field.
- The job must persist artifacts to the declared Hub repository before its ephemeral container exits.
- Scheduled Jobs, webhooks, Inference Endpoints, and Inference Providers are documented provider surfaces, not current bridge commands.

### fal

- Discover the project's guarded fal wrapper on `PATH` and run its documented readiness check. Load the installed fal guidance for the selected Model API, Serverless, or Compute surface. Record provider-native or project-owned execution separately from `cloud-bridge` support. Paid execution requires a wrapper or controller that implements duplicate control, budget enforcement, and output receipts.
- Identify the exact fal surface before choosing an adapter. Do not force a project-owned `fal-serverless` app/controller through this bridge's generic `fal_queue_v1` slot, and do not assume the generic slot is the only valid fal execution path.
- Treat fal queue inference as `managed_inference`, not as a rented Pod. Do not require images, SSH, ports, machine telemetry, or Pod deletion unless the chosen fal surface actually deploys or retains a persistent resource.
- A guarded path records the exact model/endpoint and input ledger, stable item IDs, request IDs, concurrency and retry policy, a request-start deadline distinct from total supervision time, terminal status, downloaded outputs, validation results, SHA-256 hashes, and cost evidence.
- Bound spend with the authorized item count, per-item or aggregate cost ceiling, retry ceiling, and concurrency limit. Preserve a frozen batch ledger and suppress duplicate submissions when resuming.
- On abort or timeout, cancel queued or running requests when the API supports it and record the result. Download ephemeral provider outputs promptly; a returned URL or successful request status alone is not artifact proof.
- Verify the selected endpoint, input schema, spend cap, data policy, and closeout behavior against the execution contract. Use the established cost basis for the authorized workload; refresh it when the endpoint, machine shape, billing evidence, or scope changes. Keep credentials and account-specific configuration in runtime references.
- If a project already supplies this guarded path, use and validate it. If the user asks to build or finish it, implementation and tests are in scope; the bridge registry's old support label is not a reason to defer the work or change providers.

## Public provider knowledge

Start with `docs/providers/README.md`. Use official documentation or release notes for time-sensitive facts, link the source near the claim, and add a review date. Keep provider capability separate from bridge support: a documented provider feature is not implemented until the registry and guarded runner say so.

Record only sanitized, reusable provider lessons. Do not publish raw provider responses, local paths, account details, one-off prices, or organization-specific operating notes. See `references/self-learning.md` for the public contribution rules.

## References

- `references/cli-reference.md`
- `references/contract-checklist.md`
- `references/runpod-operations.md`
- `references/worker-readiness.md`
- `references/self-learning.md`
- `docs/provider-adapter-contract.md`
- `docs/providers/README.md`
- `docs/providers/runpod.md`
- `docs/providers/huggingface.md`
- `docs/providers/neoclouds.md`
