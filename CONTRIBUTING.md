# Contributing

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The package uses only the Python standard library at runtime.

## Validation

Run these before opening a pull request:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
bin/cloud-bridge public-audit
bin/cloud-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/cloud-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/cloud-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
bin/cloud-bridge validate-manifest examples/hf-job/launch_manifest.json
bin/cloud-bridge run-job examples/hf-job/launch_manifest.json --out-dir .runtime/hf-job-dryrun --max-spend-usd 0.05
```

`run-job` is the Hugging Face Jobs surface (the second provider with automated launch support); without `--execute` it renders the submit request with no API call. See [docs/providers/huggingface.md](docs/providers/huggingface.md).

## Adding a Provider

By default a new provider is setup guidance only: it captures launch constraints, auth, monitoring, artifact movement, cost, and cleanup, and it rejects paid launch through the bridge until a first public smoke validates it. To add one:

1. Create `src/cloud_bridge/providers/<name>/{__init__.py, adapter.py}`. Subclass `ProviderAdapter` (`providers/base.py`) and set `name`, `adapter_id`, `automated_launch = False`, a `category` (`compute_rental`, `managed_inference`, or `notebook_job`, which fix cleanup and budget semantics; see [docs/provider-adapter-contract.md](docs/provider-adapter-contract.md)), a `provenance` string, a one-paragraph `summary`, a `learnings_doc` path, a `roadmap` list, and a `capabilities()` method. Add a `known_patterns` list when you have concrete launch patterns to record.
2. Register it: import the class in `providers/registry.py` and add an instance to the registration tuple.
3. Document it: add or extend a doc under `docs/providers/` and point `learnings_doc` at it; add a row to the table in `docs/provider-adapter-contract.md`.
4. Keep it honest: a setup-only entry says so in its docstring and summary and carries a provenance that names the source, such as `researched from official docs (2026-06); not yet run through this bridge`. Do not claim a pattern is validated until a first public smoke validates it. Set `automated_launch = True` only once guarded launch, monitoring, artifact capture, and cleanup commands exist.
5. Verify: `cloud-bridge providers` and `cloud-bridge provider-capabilities <name>` should show it, the full test suite stays green, and `cloud-bridge public-audit` stays clean. Keep internal tokens and secrets out by building any forbidden test literals through string concatenation.

Whether a provider earns a registered entry or a reference-doc note is governed by the provider-entry criterion in the contract doc.

## Remote RunPod Changes

Do not run paid remote resources in CI. Remote smoke tests must:

- use a dedicated cheap manifest
- set `--max-spend-usd`
- record the resource record
- delete or stop the pod during closeout
- avoid committing generated `runpod-execution/` packets
