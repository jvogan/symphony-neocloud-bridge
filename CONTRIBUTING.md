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
bin/runpod-bridge public-audit
bin/runpod-bridge validate-manifest templates/runpod-launch-manifest.template.json
bin/runpod-bridge validate-manifest examples/cheap-pod/launch_manifest.json
bin/runpod-bridge run-local examples/cheap-pod/launch_manifest.json --repo-dir .runtime/cheap-pod-repo --runtime-dir .runtime/cheap-pod-run
```

## Remote RunPod Changes

Do not run paid remote resources in CI. Remote smoke tests must:

- use a dedicated cheap manifest
- set `--max-spend-usd`
- record the resource record
- delete or stop the pod during closeout
- avoid committing generated `runpod-execution/` packets
