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

`run-job` is the Hugging Face Jobs surface (the second implemented provider); without `--execute` it renders the submit request with no API call. See [docs/providers/huggingface.md](docs/providers/huggingface.md).

## Adding a Provider Slot

A "slot" is a documented, not-yet-executable adapter. To add one:

1. Create `src/cloud_bridge/providers/<name>/{__init__.py, adapter.py}`. Subclass `ProviderAdapter` (`providers/base.py`); set `name`, `adapter_id`, `implemented = False`, a `category` (`compute_rental` | `managed_inference` | `notebook_job` — these fix cleanup/budget semantics; see [docs/provider-adapter-contract.md](docs/provider-adapter-contract.md)), a `provenance` string, a one-paragraph `summary`, `known_patterns`, `roadmap`, and a `capabilities()` method.
2. Register it: import the class in `providers/registry.py` and add an instance to the registration tuple.
3. Document it: add or extend a doc under `docs/providers/` and point `learnings_doc` at it; add a row to the table in `docs/provider-adapter-contract.md`.
4. Keep it honest: slots seeded from docs must say so in the docstring/summary and carry a `slot (researched)` provenance. Never claim a pattern is validated until a first public smoke validates it.
5. Verify: `cloud-bridge providers` and `cloud-bridge provider-capabilities <name>` should show it, the full test suite stays green, and `cloud-bridge public-audit` stays clean (no internal tokens/secrets — build forbidden literals by string concatenation in tests).

Whether a provider earns a registered slot vs a doc-note is governed by the slotting criterion in the contract doc.

## Remote RunPod Changes

Do not run paid remote resources in CI. Remote smoke tests must:

- use a dedicated cheap manifest
- set `--max-spend-usd`
- record the resource record
- delete or stop the pod during closeout
- avoid committing generated `runpod-execution/` packets
