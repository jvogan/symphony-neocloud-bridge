import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research-table-qc"


class ResearchTableExampleTests(unittest.TestCase):
    def run_stage(self, stage, source, output):
        return subprocess.run(
            [sys.executable, str(EXAMPLE / "workflow.py"), stage,
             "--input", str(source), "--out", str(output)],
            capture_output=True, text=True, check=False,
        )

    def test_fixture_produces_expected_totals_and_report(self):
        source = EXAMPLE / "counts.csv"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for stage in ("summarize", "report", "validate"):
                result = self.run_stage(stage, source, output)
                self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((output / "qc.json").read_text())
            self.assertEqual(summary["feature_count"], 5)
            self.assertEqual(summary["all_zero_feature_count"], 1)
            self.assertEqual([s["total_count"] for s in summary["samples"]], [18, 10, 7])
            self.assertEqual([s["detected_features"] for s in summary["samples"]], [3, 3, 3])
            self.assertEqual(summary["input_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertIn("| sample_a | 18 | 3 |", (output / "report.md").read_text())

    def test_invalid_counts_and_identifiers_produce_no_summary(self):
        tables = [
            "feature_id,a\ngene1,-1\n",
            "feature_id,a\ngene1,NaN\n",
            "feature_id,a\ngene1,1.5\n",
            "feature_id,a,b\ngene1,1\n",
            "feature_id,a,a\ngene1,1,2\n",
            "feature_id,a\ngene1,1\ngene1,2\n",
            "feature_id,a\n",
        ]
        for table in tables:
            with self.subTest(table=table), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source = base / "counts.csv"
                source.write_text(table)
                result = self.run_stage("summarize", source, base / "out")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((base / "out" / "qc.json").exists())

    def test_changed_inputs_and_tampered_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / "counts.csv", base / "out"
            original = (EXAMPLE / "counts.csv").read_text()
            source.write_text(original)
            self.assertEqual(self.run_stage("summarize", source, output).returncode, 0)
            self.assertEqual(self.run_stage("report", source, output).returncode, 0)
            source.write_text(original.replace("12,0,3", "13,0,3"))
            self.assertNotEqual(self.run_stage("report", source, output).returncode, 0)
            source.write_text(original)
            qc = output / "qc.json"
            summary = json.loads(qc.read_text())
            summary["samples"][0]["total_count"] = 999
            qc.write_text(json.dumps(summary))
            self.assertNotEqual(self.run_stage("validate", source, output).returncode, 0)
            self.assertEqual(self.run_stage("summarize", source, output).returncode, 0)
            (output / "report.md").write_text("changed report\n")
            self.assertNotEqual(self.run_stage("validate", source, output).returncode, 0)


if __name__ == "__main__":
    unittest.main()
