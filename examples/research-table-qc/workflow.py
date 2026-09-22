"""Validate a count table, summarize samples, and render a checked report."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re


IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")
COUNT = re.compile(r"[0-9]+")


def summarize(source: Path) -> dict:
    raw = source.read_bytes()
    rows = csv.reader(io.StringIO(raw.decode("utf-8")))
    header = next(rows, [])
    if len(header) < 2 or header[0] != "feature_id":
        raise ValueError("CSV header must start with feature_id and include sample columns")
    samples = header[1:]
    if len(set(samples)) != len(samples) or not all(IDENTIFIER.fullmatch(s) for s in samples):
        raise ValueError("sample identifiers must be unique and use letters, digits, dots, underscores, or hyphens")
    totals, detected = [0] * len(samples), [0] * len(samples)
    features: set[str] = set()
    zero_rows = 0
    for line, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise ValueError(f"row {line}: column count differs from the header")
        feature = row[0]
        if not IDENTIFIER.fullmatch(feature) or feature in features:
            raise ValueError(f"row {line}: feature identifier is invalid or repeated")
        if not all(COUNT.fullmatch(value) for value in row[1:]):
            raise ValueError(f"row {line}: counts must be nonnegative integers")
        values = [int(value) for value in row[1:]]
        features.add(feature)
        zero_rows += int(not any(values))
        for i, value in enumerate(values):
            totals[i] += value
            detected[i] += int(value > 0)
    if not features:
        raise ValueError("the count table must contain at least one feature row")
    return {
        "schema_version": 1,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "feature_count": len(features),
        "all_zero_feature_count": zero_rows,
        "samples": [
            {"sample_id": sample, "total_count": totals[i], "detected_features": detected[i]}
            for i, sample in enumerate(samples)
        ],
    }


def checked_summary(source: Path, output: Path) -> dict:
    summary = json.loads((output / "qc.json").read_text())
    if summary != summarize(source):
        raise ValueError("qc.json does not match the input table; rerun summarize")
    return summary


def render_report(summary: dict) -> str:
    lines = [
        "# Expression-table checks",
        "",
        f"Features: {summary['feature_count']}. Samples: {len(summary['samples'])}.",
        f"Features with zero counts in every sample: {summary['all_zero_feature_count']}.",
        "",
        "| Sample | Total count | Detected features |",
        "| --- | ---: | ---: |",
    ]
    for sample in summary["samples"]:
        lines.append(f"| {sample['sample_id']} | {sample['total_count']} | {sample['detected_features']} |")
    lines += ["", f"Input SHA-256: `{summary['input_sha256']}`.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("summarize", "report", "validate"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.stage == "summarize":
        summary = summarize(args.input)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "qc.json").write_text(json.dumps(summary, indent=2) + "\n")
    else:
        summary = checked_summary(args.input, args.out)
        report = render_report(summary)
        if args.stage == "report":
            (args.out / "report.md").write_text(report)
        elif (args.out / "report.md").read_text() != report:
            raise ValueError("report.md does not match qc.json; rerun report")
    print(f"{args.stage}: passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error) as error:
        raise SystemExit(f"Input or artifact check failed: {error}")
