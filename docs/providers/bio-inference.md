# Biological inference provider guidance

Last reviewed: 2026-08-30.

Boltz, EvolutionaryScale ESM, and NVIDIA NIM have registered setup guides. Use these references to define the model interface and result checks for an agent's research workflow. Automated execution through these bridge adapters remains unimplemented.

| Provider | Adapter | Contract focus |
| --- | --- | --- |
| Boltz | `boltz_api_v1` | Structured inputs, asynchronous prediction, result retrieval, and scientific claim boundaries |
| ESM | `esm_forge_v1` | Sequence inputs, model/task selection, output normalization, and usage limits |
| NVIDIA NIM | `nvidia_nim_v1` | Exact model endpoint, request schema, deployment ownership, and returned structure artifacts |

Record the input hash, model version, parameters, and output files for each request. Define scientific checks in the analysis project, alongside the checks for file completeness and format. This gives later tools a traceable input and the results of the relevant validation.

Future adapters must pin the provider surface and model version, validate input policy, bound requests and retries, retrieve durable outputs, hash raw and normalized artifacts, and keep scientific interpretation in the domain workflow.

See [agent workflows](../agent-workflows.md) for artifact handoffs and a runnable expression-table example.

Sources: [Boltz documentation](https://docs.boltz.bio/), [EvolutionaryScale Forge](https://forge.evolutionaryscale.ai/), [NVIDIA NIM documentation](https://docs.nvidia.com/nim/).
