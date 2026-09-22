# Provider knowledge base

Use this directory to select compute and check bridge support. Provider capabilities and bridge automation have separate status: a cloud may offer a feature before the bridge implements it.

Index and support matrix reviewed: 2026-09-22. Individual provider pages carry their own review dates.

| Task | Reference |
| --- | --- |
| Compare providers and execution models | [Compute landscape](compute-landscape.md) |
| Find searchable compute databases and benchmarks | [Compute directories](compute-directories.md) |
| Estimate cost and define recovery and cleanup | [Selection and operating guide](selection-guide.md) |
| Inspect exact executable support | `cloud-bridge providers` and `cloud-bridge provider-capabilities <provider>` |
| Implement a guarded provider path | [Adapter contract](../provider-adapter-contract.md) |

## Support levels

| Provider | Adapter | Category | Support | Reference |
| --- | --- | --- | --- | --- |
| RunPod | `runpod_pod_v1` | `compute_rental` | Automated | [RunPod](runpod.md) |
| Hugging Face | `huggingface_v1` | `notebook_job` | Automated for Jobs; setup guidance for other surfaces | [Hugging Face](huggingface.md) |
| AWS | `aws_v1` | `compute_rental` | Setup guidance with rendered plans | [AWS](aws.md) |
| Modal | `modal_function_v1` | `compute_rental` | Setup guidance | [Modal](modal.md) |
| Lambda Cloud | `lambda_cloud_vm_v1` | `compute_rental` | Setup guidance | [Lambda Cloud](lambda.md) |
| Beam | `beam_function_v1` | `compute_rental` | Setup guidance | [Neoclouds](neoclouds.md) |
| fal | `fal_queue_v1` | `managed_inference` | Setup guidance | [Neoclouds](neoclouds.md) |
| Replicate | `replicate_prediction_v1` | `managed_inference` | Setup guidance | [Neoclouds](neoclouds.md) |
| Together | `together_v1` | `managed_inference` | Setup guidance | [Neoclouds](neoclouds.md) |
| Boltz | `boltz_api_v1` | `managed_inference` | Setup guidance | [Biological inference](bio-inference.md) |
| ESM | `esm_forge_v1` | `managed_inference` | Setup guidance | [Biological inference](bio-inference.md) |
| NVIDIA NIM | `nvidia_nim_v1` | `managed_inference` | Setup guidance | [Biological inference](bio-inference.md) |
| Kaggle | `kaggle_kernel_v1` | `notebook_job` | Setup guidance | [Notebook compute](notebook-compute.md) |
| Google Cloud | `gcp_vertex_v1` | `notebook_job` | Setup guidance | [Notebook compute](notebook-compute.md) |

The registry enforces executable support. Providers in the broader [landscape](compute-landscape.md) can be research candidates without a registry entry.

## How to read an entry

Each entry separates three kinds of information:

- **Implemented:** the bridge has a guarded command for this exact provider surface.
- **Documented:** an official provider source describes the behavior, but the bridge has not exercised it.
- **Planned:** the surface is a compatibility or adapter target, not a current capability.

Prices, quotas, regions, hardware catalogs, and beta features can change quickly. Treat dated values as sizing context and recheck the provider before spending.

## Compatibility watch

RunPod migration and Hugging Face Jobs sources checked: 2026-09-22.

### RunPod

- REST API v2 is available at `https://api.runpod.io/v2`. RunPod has deprecated REST API v1 and documents retirement on November 15, 2026. The current bridge runner still uses v1, so migration is required before that date.
- API v2 adds standardized resource shapes, catalog endpoints, Pod and Serverless log streaming, broader billing, and Instant Cluster resources.
- `runpodctl` v2.10.0 added Pod and Serverless log streaming. Version 2.12.0 removed `--stop-after` and `--terminate-after`; do not use those flags as a cleanup backstop.
- The official RunPod plugin v1.2.0 added a migration skill for GraphQL and REST v1 integrations moving to v2.

Primary sources: [API v2 overview](https://docs.runpod.io/api-reference-v2/overview), [v1 migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1), [`runpodctl` releases](https://github.com/runpod/runpodctl/releases), [official RunPod plugin](https://github.com/runpod/runpod-plugins-official), and [RunPod release notes](https://docs.runpod.io/release-notes).

### Hugging Face Jobs

- Jobs are available through the `hf` CLI, `huggingface_hub`, and the Jobs HTTP API.
- The public Jobs surface includes scheduling, webhook-triggered jobs, metrics, log retrieval, and volume support. The bridge's automated path currently implements one-shot submit, poll, log capture, Hub-repository artifact verification, and cancel-on-abort; scheduled jobs and webhook creation are not bridge commands.

Primary sources: [Jobs overview](https://huggingface.co/docs/hub/jobs), [Jobs reference](https://huggingface.co/docs/hub/jobs-reference), [scheduled jobs](https://huggingface.co/docs/hub/jobs-schedule), and [webhook automation](https://huggingface.co/docs/hub/jobs-webhooks).

## Updating the knowledge base

1. Use official documentation, official release notes, official SDK references, or the provider's public repository.
2. Record the review date and link the source near the claim.
3. Keep support status separate from provider capability. A provider feature is not a bridge feature until a guarded path exists.
4. Prefer stable lifecycle, billing, artifact, and cleanup facts over marketing claims or rankings.
5. Keep provider pages concise and free of credentials, account identifiers, personal paths, raw errors, private data, and organization-specific run details.

See [the provider adapter contract](../provider-adapter-contract.md) for the executable support rules.
