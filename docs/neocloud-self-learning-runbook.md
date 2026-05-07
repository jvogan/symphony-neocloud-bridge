# Neocloud Self-Learning Runbook

Use this runbook when a RunPod or future neocloud run is slow, ambiguous, failed, manually rescued, unexpectedly expensive, or merely awkward enough that the next agent should not have to rediscover the same lesson.

The goal is not to publish run diaries. The goal is to turn operational friction into public-safe bridge improvements: better manifests, stronger checks, clearer examples, tighter failure playbooks, and cheaper smoke ladders.

## Safety Boundary

Never commit, paste, or summarize private run material directly.

Keep out of repo files, issues, comments, docs, and chat logs:

- API keys, registry credentials, SSH keys, presigned URLs, and one-time transfer codes.
- Pod IDs, network volume IDs, private repo names, private image names, private Linear issue text, customer or process records, unpublished data, and raw logs.
- Generated `runpod-execution/` packets, artifact hashes from private runs, closeout files, dashboards, cost ledgers, or private incident timelines.

Keep only generalized mechanics:

- Provider behavior shape, such as "HTTP proxy returned a provider 403 before artifact server was reachable."
- Bridge behavior shape, such as "manifest allowed a completion-only artifact server but no live progress channel."
- Reusable mitigation, such as "require `productivity-plan` before paid long runs."
- Synthetic or public demo examples that reproduce the lesson without private inputs.

## When To Open A Learning Loop

Start a learning loop after any of these:

- paid resource launched later than expected, failed to boot, or needed a manual retry
- pod reached RUNNING but workload progress was unclear
- runtime metrics contradicted workload status
- startup payload was too large or close to a provider limit
- image lacked a bootstrap dependency such as `git`, shell tools, CUDA runtime, or AWS CLI
- artifact egress was missing, fragile, private, or too slow
- cleanup needed manual intervention or closeout could not prove cleanup
- cost exceeded estimate, was unavailable, or came from the wrong cost surface
- a worker sandbox could prepare a run but could not reach provider APIs
- an agent needed operator judgment that could have been encoded as a gate, manifest field, test, or doc

## Learning Loop

1. **Stop spend first.** If there is no live productivity channel and no immediate operator peek path, clean up or stop at the handoff boundary before running another paid attempt.
2. **Classify the layer.** Use one primary layer: issue authorization, worker environment, manifest contract, provider create, scheduling, image bootstrap, workload command, progress signal, artifact egress, cost closeout, or cleanup.
3. **Write a private scratch note outside the repo.** Record only enough to reason: symptom, phase, expected behavior, observed behavior, what evidence was available, what evidence was missing, and cleanup state.
4. **Extract the public-safe pattern.** Remove identifiers and domain details. Convert the event into a generic failure mode, trigger, and mitigation.
5. **Choose the smallest durable improvement.** Prefer a validation check or test when the bridge can catch it. Prefer docs when the issue is judgment or operator workflow. Prefer an example manifest when users need a copyable pattern.
6. **Run the smallest proof.** Use local validation first. If a paid smoke is necessary, use the cheapest manifest that exercises one variable and includes `--max-spend-usd`, artifact proof, and cleanup.
7. **Promote the lesson.** Update the right surface: CLI validation, manifest template, synthetic example, failure playbook, worker readiness, observability ladder, release checklist, or this runbook.
8. **Close the loop.** The final note should say what changed, how it was validated, and what future run should behave differently.

## Hiccup Intake Template

Use this shape for private scratch notes, then copy only the public-safe pattern into repo docs or tests.

```markdown
## Hiccup

- Date:
- Provider or adapter:
- Worker lane: Codex, Claude Code, mixed, or operator
- Phase: authorization, preflight, create, boot, workload, progress, egress, cost, cleanup
- Symptom:
- Expected:
- Observed evidence:
- Missing evidence:
- Spend or runtime impact:
- Cleanup state:
- Public-safe pattern:
- Candidate improvement:
- Smallest validation:
```

## DoE For Neocloud Hiccups

Treat provider learning like a small design-of-experiments loop. Change one meaningful variable at a time and stop as soon as the lesson is proven.

Common factors:

- adapter: pod, future serverless, future Flash, or future cluster lane
- compute profile: tiny CPU, exact CPU flavor, GPU family, GPU count
- data center or region policy
- image or template
- bootstrap mode: inline, git remote, snapshot, packet, object store
- progress channel: `/healthz`, SSH/log tail, status packet, heartbeat file
- artifact egress: HTTP proxy, direct TCP, SCP, network volume, presigned S3, object store
- cleanup mode: stop, delete, retain volume, retain endpoint

Canary ladder:

1. **Local contract only.** `validate-manifest`, `contract-self-check`, `preflight`, `productivity-plan`, and `egress-plan`.
2. **Tiny provider smoke.** Cheapest CPU run that proves create, startup, heartbeat, artifact hash, closeout, and cleanup.
3. **Exact image smoke.** Same tiny workload inside the intended image or template.
4. **Egress smoke.** Minimal artifact through the intended durable egress path.
5. **GPU or accelerator smoke.** `nvidia-smi` or equivalent plus a minimal status endpoint before real GPU code.
6. **Representative workload slice.** Small real input, strict budget, resume policy, and declared artifacts.
7. **Full workload.** Only after the earlier rung proves the route.

Measure:

- create latency
- time to first heartbeat
- time to first useful log
- payload byte size
- artifact egress time and size
- cost estimate versus billed cost
- cleanup confirmation time
- number of manual operator decisions

## Promotion Targets

Use the narrowest durable target:

- **CLI validation** when the bridge can reject a bad manifest before spend.
- **Unit test** when a bug or policy should never regress.
- **Manifest template** when a field should become part of the standard contract.
- **Synthetic example** when users need a copyable pattern.
- **Failure playbook** when the symptom-to-action mapping is reusable.
- **Worker readiness docs** when the lesson is about Codex, Claude Code, or orchestrator capability.
- **Observability ladder** when the lesson is about provider state versus workload truth.
- **Remote smoke runbook** when the lesson changes the paid-smoke sequence.
- **Provider adapter contract** when the lesson generalizes beyond RunPod pods.

## Public-Safe Learning Examples

Good public lesson:

```text
Completion-only artifact servers do not prove live productivity. Long runs need a fresh progress channel before paid launch.
```

Good public improvement:

```text
Add `productivity-plan` to the pre-launch checklist for large or expensive workloads.
```

Bad public lesson:

```text
Private issue TEAM-123 failed on pod <real-pod-id> while running <private-repo> against <private-dataset>.
```

## Closeout Questions

Before declaring the learning loop done, answer:

- Could a future agent catch this locally before paid launch?
- If not, is there a cheaper smoke that catches it before the full workload?
- Did the improvement reduce operator judgment or merely document it?
- Is the lesson free of secrets, private paths, private identifiers, and raw run artifacts?
- Did `public-audit` and relevant tests still pass?

