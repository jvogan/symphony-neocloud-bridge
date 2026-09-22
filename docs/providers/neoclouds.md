# Managed inference and neocloud guidance

Last reviewed: 2026-08-30.

The providers on this page are setup guidance for this bridge. They do not have guarded launch commands in the bridge; provider-native execution remains a separate path and must satisfy its own authorization, budget, artifact, retry, and closeout contract. Their entries capture lifecycle questions that a future adapter must answer without turning changing prices, quotas, or marketing claims into repository policy.

| Provider | Registry adapter | Lifecycle to model | Important boundary |
| --- | --- | --- | --- |
| fal | `fal_queue_v1` | Request queue and result retrieval | A request start deadline is not a total inference timeout; retry behavior must be explicit. |
| Together | `together_v1` | Serverless request or asynchronous batch | Batch completion and output retrieval need an evidence contract; persistent endpoints need separate cleanup. |
| Replicate | `replicate_prediction_v1` | Prediction or persistent deployment | API prediction data has a limited retention window; deployments can keep capacity active. |
| Beam | `beam_function_v1` | Function, task queue, or persistent deployment | Keep-warm and deployment settings can leave billable capacity active. |

## fal

The queue API returns a request identifier that can be polled until a result is available. `X-Fal-Request-Timeout` controls how long a request may wait to start, not the full inference duration. The platform documents automatic retries and an `X-Fal-No-Retry` override. A bridge adapter should record retry policy, total supervision time, result lifecycle, and output hashes.

Sources: [queue API](https://fal.ai/docs/documentation/model-apis/inference/queue), [retries](https://fal.ai/docs/documentation/serverless/reliability/retries).

## Together

Together documents an asynchronous Batch API built around uploaded input, batch submission, status polling, and output retrieval. A future adapter should distinguish stateless requests and batches from any persistent endpoint product, and it should validate returned outputs before closeout.

Source: [Together Batch API](https://docs.together.ai/docs/inference/batch/overview).

## Replicate

Replicate predictions have a bounded lifecycle and API-created prediction data is retained for a limited period by default. Artifact retrieval must therefore happen promptly. Deployments are a separate persistent surface; setting minimum and maximum instances to zero disables a deployment and stops its serving capacity.

Sources: [prediction lifecycle](https://replicate.com/docs/topics/predictions/lifecycle/), [data retention](https://replicate.com/docs/topics/predictions/data-retention/), [deployment controls](https://replicate.com/docs/topics/deployments/view-deployments).

## Beam

Beam exposes functions, task queues, endpoints, and other deployment shapes. Its keep-warm configuration affects how long capacity remains available after work. A future adapter must make that setting visible, undeploy persistent resources when required, and verify that closeout leaves no unintended warm capacity.

Source: [Beam keep-warm configuration](https://docs.beam.cloud/v2/endpoint/keep-warm).

## Promotion requirements

Before any of these entries becomes automated, add a provider-specific client and tests for authorization, spend limits, duplicate suppression, terminal-state handling, artifact retrieval, retries, and cleanup. Then update the registry support label. Documentation alone is never launch authorization.
