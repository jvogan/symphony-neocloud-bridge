# Product brief

Neocloud Bridge gives AI agents reusable tools for cloud execution. Agents supply workload commands and expected results; the bridge manages supported provider runs and returns validated artifacts, costs, and cleanup records.

## Who uses it

- Researchers running biological data analysis, model evaluation, or simulations with an AI agent.
- Developers connecting local tools, cloud jobs, and managed inference in one workflow.
- Teams that need repeatable run contracts across projects and compute providers.
- Contributors adding provider guides or executable adapters.

## Capabilities

| Need | Bridge capability |
| --- | --- |
| Choose compute | Provider references, execution-model comparisons, and compute directories |
| Prepare a run | Manifest validation, contract self-check, local preflight, and provider-specific planning |
| Execute work | Automated RunPod Pods and one-shot Hugging Face Jobs |
| Connect tools | Ordered commands, output contracts, validation commands, and artifact hashes |
| Recover work | Run records, workload monitoring, checkpoint declarations, and recovery guidance |
| Account for resources | Spend limits, cost records, cancellation or termination, and retention records |

## Workflow ownership

Your agent or workflow engine sequences stages, selects scientific tools, and evaluates their results. The bridge executes each supported provider run. A stage can contain several commands; workflows that span providers use separate manifests and pass validated artifacts between runs.

The [research-table example](../examples/research-table-qc/) demonstrates a local chain with a synthetic expression matrix, a machine-readable summary, and a report. The [agent workflow guide](agent-workflows.md) shows how to structure larger projects around the same artifact handoffs.

## Success criteria

A completed run has the required artifacts, passing validation, recorded hashes, cost evidence, and verified cleanup or approved retention. Provider status and workload progress remain separate observations. Provider capability output matches executable adapter support.

## Integrations

The CLI works with agents that can invoke shell commands. The bundled skill provides the same workflow as agent instructions. Optional AWS features render orchestration plans for storage, registry access, queues, locks, and cleanup schedules. Symphony and Linear provide optional dispatch and issue-tracking integration.

The existing package name, skill invocation, manifest fields, and outcome format remain compatible with existing installations.
