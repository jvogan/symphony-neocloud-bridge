# Public compute directories and databases

Last reviewed: 2026-09-22.

Public directories support discovery. They expose candidate providers, shapes, prices, and measurements, but a row is neither a capacity reservation nor a complete workload cost. For capacity, location, storage, egress, idle time, and cleanup checks, see the [compute selection guide](selection-guide.md).

## Source comparison

| Need | Start with | What it provides | Main constraint |
| --- | --- | --- | --- |
| Broad CPU and GPU inventory | [Spare Cores Navigator](https://sparecores.com/) | Normalized compute, storage, traffic, region, price, and benchmark records | Snapshot and service terms differ; some public dumps omit large tables |
| Dated GPU price history | [GetDeploying](https://getdeploying.com/dataset/gpu-prices) | CSV/JSON history plus a documented per-provider API | Advertised listings are not verified rentable capacity; API access is paid |
| Current normalized GPU rows | [GPUs.io](https://gpus.io/) | API rows for prices, configurations, regions, availability, and `lastUpdated` | Paid API and history plans; regions are country codes |
| Human GPU discovery | [Cloud-GPUs.com](https://cloud-gpus.com/) | Searchable provider links with model, VRAM, region, compliance, and billing filters | No documented public export or data license |
| Reproducible offline analysis | [OpenComputePrices](https://github.com/thatkavish/OpenComputePrices) | Downloadable release archives with source timestamps and normalized rental rows | The latest archive changes; save a copy and hash it |
| Orchestration candidates | [SkyPilot Catalog](https://github.com/skypilot-org/skypilot-catalog) | VM and accelerator inputs used by SkyPilot placement | An orchestration catalog, not a neutral benchmark or capacity feed |
| Inference quality and price | [Artificial Analysis](https://artificialanalysis.ai/) | Model and inference-endpoint quality, latency, throughput, and token-price data | Inference measurements and token prices are not rentable GPU-hour data |

Update intervals and availability markers are source claims; retain the timestamp and provider link.

## Spare Cores Navigator

[Spare Cores](https://sparecores.com/) combines a comparison site, a public Navigator API, downloadable database snapshots, and open-source collection tools. Its public description covers CPU, memory, storage, GPU, networking, regions, prices, benchmarks, and cost-efficiency metrics. The [SC Crawler documentation](https://sparecores.github.io/sc-crawler/) describes the schemas and collection workflow.

The [data-dumps repository](https://github.com/SpareCores/sc-data-dumps) publishes JSON table dumps and links to a compressed SQLite snapshot. It documents spot-price updates for most vendors every five minutes and other vendor data hourly, with slower Azure lookups. The JSON subset omits pricing and benchmark tables because of size and update frequency.

Published data records use [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), while the full SQLite snapshot uses [BSL 1.1](https://sparecores.com/legal/license-bsl). The BSL terms and [Navigator terms](https://sparecores.com/legal/terms-of-service) restrict some production, systematic-extraction, and redistribution uses. Public data is indicative and may be incomplete or stale.

## GetDeploying

[GetDeploying's API](https://getdeploying.com/help/api) documents normalized GPU offerings, price history, GPU models, and providers. Its documented refresh range is 15 minutes to daily. Fields include GPU model and count, VRAM, memory bandwidth, interconnect, CPU, system RAM, disk, billing type, hourly and monthly estimates, an availability label, and `last_verified`. The API requires a subscription.

The free [GPU rental price-history dataset](https://getdeploying.com/dataset/gpu-prices) provides weekly aggregates in CSV and JSON and states that it updates daily. The files are [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) with attribution.

GetDeploying says aggregates include priced listings not marked sold out, including unknown or waitlisted listings, rather than checked rentable capacity. Regional price differences may be missing. Use the export for market trends and the API for more granular history, then consult the provider for the exact offer.

## GPUs.io

[GPUs.io](https://gpus.io/) provides a comparison table and documented read-only REST endpoints for current prices, price history, GPU types, and providers. Its [API documentation](https://gpus.io/docs) describes rows with normalized USD per GPU-hour, total configuration price, GPU count, CPU and RAM, boot and scratch disk, regions, availability fields, and `lastUpdated`. Region values are ISO country codes, not provider-specific zones.

The [methodology](https://gpus.io/en/methodology) says the service combines provider documentation, pricing pages, and provider APIs where available, then normalizes GPU names and billing units. The API requires a paid key; plan quotas and history access are described in the [rate limits](https://gpus.io/docs/rate-limits) and [price history](https://gpus.io/docs/price-history) pages. Its [terms](https://gpus.io/en/terms) prohibit selling, licensing, syndicating, or redistributing the data as a standalone dataset, feed, or comparison service. Treat `available` as a listing field, not a reservation.

## Cloud-GPUs.com

[Cloud-GPUs.com](https://cloud-gpus.com/) is a human-facing discovery index. It offers shareable filters for GPU model, VRAM, provider, region, compliance, CUDA compatibility, and on-demand, spot, or reserved billing, with links to the provider. The site displays a last-updated marker, but does not document a public API or reusable export.

Its [public repository](https://github.com/cloud-gpus/cloud-gpus.github.io) was archived in 2025, so the old repository is not evidence of the current site's data pipeline. The site discloses affiliate links, and its [terms](https://cloud-gpus.com/terms/) do not grant a public data license. Use it for candidate discovery and follow the provider link for current details.

## OpenComputePrices

[OpenComputePrices](https://github.com/thatkavish/OpenComputePrices) supports offline analysis through GitHub release archives and collector code. Its normalized rows include source and collection timestamps, GPU variant and count, VRAM, CPU, RAM, storage, network, region, zone, country, billing type, price, and availability when known. The workflow documents scheduled API, scraper, and browser runs, recent-data retention, and monthly archives. The schema separates `on_demand`, `spot`, `reserved`, and `inference`; filter to rental classes when comparing GPU-hour resources.

The project combines provider APIs, rendered pricing pages, marketplaces, and other public catalogs, so scrape failures and normalization errors can affect individual rows. The repository states an MIT license for the project; check provider terms before redistributing extracted data. Its workflow updates the same `latest-data` release. For reproducible analysis, save the archive, retrieval timestamp, and SHA-256 hash.

## SkyPilot Catalog

[SkyPilot's catalog repository](https://github.com/skypilot-org/skypilot-catalog) is an orchestration input. Its README identifies active schema versions and documents a seven-hour refresh cadence. Catalog fields include instance type, vCPUs, memory, accelerator name and count, GPU information, region, availability zone, on-demand price, spot price, architecture, and local-disk fields. [SkyPilot's GPU guide](https://docs.skypilot.ai/en/latest/compute/gpus.html) identifies this catalog as the data behind `sky gpus list`.

The catalog helps SkyPilot enumerate feasible VM shapes and optimize placement. It does not measure application performance, reserve capacity, or include every storage, egress, tax, image, or idle-charge rule. The README says AWS, GCP, and Lambda data are fetched from provider data, while other clouds require their own fetchers and actions. It does not state a separate catalog-data license; do not assume the software license covers every row.

## Artificial Analysis

[Artificial Analysis](https://artificialanalysis.ai/data-api) covers model and inference-provider data. Its API documents model metadata, benchmark scores, token pricing, provider availability, latency, throughput, and performance distributions. Its [methodology](https://artificialanalysis.ai/methodology) measures the end-to-end experience of customers using an inference service or system, not the maximum performance of particular hardware.

Use it to compare inference endpoints by quality, latency, throughput, and token cost. Its prices are token prices or weighted task costs, not VM or GPU-hour rental rates. API access is key-based and tiered. The [data API page](https://artificialanalysis.ai/data-api) offers free internal use with attribution for eligible organizations; Free and Pro plans require fewer than 150 employees. Redistribution requires an appropriate commercial package.

## Use boundary

Preserve the directory URL, provider, GPU variant and count, rental class, region, source timestamp, and any `lastUpdated` or `last_verified` field in a candidate record. Directory evidence informs selection; the provider's current offer and this bridge's manifest, support gate, and self-check control a paid run. Use the [compute selection guide](selection-guide.md) for the checks that directory rows cannot settle.
