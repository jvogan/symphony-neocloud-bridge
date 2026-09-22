# Check an expression-count table

This example runs two tools in sequence: a count-table summarizer and a report renderer. Both use Python's standard library. The input contains five synthetic features and three samples.

## Run through the bridge

From the repository root:

```bash
bin/cloud-bridge validate-manifest examples/research-table-qc/launch_manifest.json
bin/cloud-bridge contract-self-check examples/research-table-qc/launch_manifest.json
python3 examples/research-table-qc/run_example.py
```

The wrapper creates a fresh workspace under `.runtime/`, copies the two workload files, and invokes `cloud-bridge run-local`. Each run preserves its outputs and receipts. This example requires no cloud credentials or issue tracker. Its manifest selects the RunPod contract for local execution; remote launch is disabled.

## Inspect the result

The command prints its run directory. Within that directory, inspect `runpod-execution/`:

| File | Contents |
| --- | --- |
| `artifacts/qc.json` | Input SHA-256, feature count, all-zero feature count, and per-sample totals |
| `artifacts/report.md` | Human-readable summary table |
| `artifact_hashes.json` | Output hashes recorded by the bridge |
| `closeout.json` | Run status and artifact checks |

The fixture has five features and one all-zero feature. Expected sample totals are:

| Sample | Total count | Detected features |
| --- | ---: | ---: |
| sample_a | 18 | 3 |
| sample_b | 10 | 3 |
| sample_c | 7 | 3 |

The summarizer requires unique feature and sample identifiers, rectangular rows, and nonnegative integer counts. The report stage checks the saved summary against the input before rendering. Final validation recomputes the summary and compares the report with it. Changed inputs or edited results fail these checks.

## Reuse the tools

To check another small count table, invoke the stages directly and choose a separate output directory:

```bash
python3 examples/research-table-qc/workflow.py summarize \
  --input path/to/counts.csv --out .runtime/my-table-qc
python3 examples/research-table-qc/workflow.py report \
  --input path/to/counts.csv --out .runtime/my-table-qc
python3 examples/research-table-qc/workflow.py validate \
  --input path/to/counts.csv --out .runtime/my-table-qc
```

The first CSV column is `feature_id`; remaining headers are sample identifiers. Identifiers use letters, digits, dots, underscores, or hyphens. The output describes table structure and count totals. Your analysis project supplies normalization, statistical methods, and scientific interpretation.

For remote work, prepare an immutable copy of your project, select its compute and output storage, and complete the provider's run contract. See [agent workflows](../../docs/agent-workflows.md) for connecting stages across providers.
