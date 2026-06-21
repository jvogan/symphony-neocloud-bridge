import tempfile
import unittest
from pathlib import Path

from cloud_bridge.linear_issue import validate_issue_file
from cloud_bridge.public_readiness import (
    scan_for_forbidden_text,
    scan_for_sensitive_text,
    validate_issue_examples,
    validate_json_files,
)


ROOT = Path(__file__).resolve().parents[1]


class PublicAuditTemplateTests(unittest.TestCase):
    def test_current_linear_issue_templates_validate(self):
        paths = [
            ROOT / "templates" / "linear-runpod-issue.md",
            ROOT / "skills" / "cloud-symphony" / "assets" / "templates" / "linear-runpod-issue.md",
        ]
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                result = validate_issue_file(path)
                self.assertTrue(result.ok, result)

    def test_public_audit_checks_skill_asset_json_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skill_templates = base / "skills" / "cloud-symphony" / "assets" / "templates"
            skill_templates.mkdir(parents=True)
            (skill_templates / "broken.json").write_text("{not-json")

            failures = validate_json_files(base)

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0]["path"],
            "skills/cloud-symphony/assets/templates/broken.json",
        )

    def test_public_audit_checks_root_and_skill_linear_issue_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root_templates = base / "templates"
            skill_templates = base / "skills" / "cloud-symphony" / "assets" / "templates"
            root_templates.mkdir(parents=True)
            skill_templates.mkdir(parents=True)
            (root_templates / "linear-runpod-issue.md").write_text("## Summary\n\nToo short.\n")
            (skill_templates / "linear-runpod-issue.md").write_text("## Summary\n\nToo short.\n")

            results = validate_issue_examples(base)

        result_by_path = {item["path"]: item for item in results}
        self.assertIn("templates/linear-runpod-issue.md", result_by_path)
        self.assertIn("skills/cloud-symphony/assets/templates/linear-runpod-issue.md", result_by_path)
        self.assertFalse(result_by_path["templates/linear-runpod-issue.md"]["ok"])
        self.assertFalse(result_by_path["skills/cloud-symphony/assets/templates/linear-runpod-issue.md"]["ok"])

    def test_public_audit_blocks_literal_secrets_and_presigned_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            docs = base / "docs"
            docs.mkdir()
            runtime_key = "RUNPOD" + "_API_KEY"
            runtime_value = "rp_" + "1234567890abcdefghijklmnop"
            signature_param = "X-Amz-" + "Signature=abc123signaturevalue"
            (docs / "bad.md").write_text(
                f"{runtime_key}={runtime_value}\n"
                f"https://s3.amazonaws.com/bucket/object?{signature_param}\n"
            )

            hits = scan_for_sensitive_text(base)

        kinds = {hit["kind"] for hit in hits}
        self.assertIn("secret_assignment", kinds)
        self.assertIn("high_confidence_secret", kinds)
        self.assertIn("presigned_url", kinds)

    def test_public_audit_flags_non_public_source_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            docs = base / "docs"
            docs.mkdir()
            source_reference = " ".join(("sibling", "private", "repos"))
            run_reference = " ".join(("prior", "internal", "runs"))
            named_reference = "Elastic" + "BLAST"
            economics_reference = " ".join(("Observed", "economics"))
            sample_reference = " ".join(("Sample", "per-run", "points"))
            burned_reference = "burned" + " $" + "1"
            campaign_reference = "4" + "-instance GPU " + "campaign"
            smoke_reference = "GH200 " + "smoke" + ": 276.9s " + "wall"
            (docs / "bad.md").write_text(
                f"Seeded from {source_reference} across {run_reference}.\n"
                f"{named_reference} details belong outside public docs.\n"
                f"{economics_reference}: {sample_reference}: canary {burned_reference}.\n"
                f"{campaign_reference}; {smoke_reference}.\n"
            )

            hits = scan_for_forbidden_text(base)

        self.assertIn("source_provenance:private_source", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:prior_run_source", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:named_run_source", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:observed_economics", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:sample_run_costs", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:burned_cost", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:instance_campaign", {hit["token"] for hit in hits})
        self.assertIn("source_provenance:wall_time_smoke", {hit["token"] for hit in hits})

    def test_public_audit_allows_documented_secret_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            docs = base / "docs"
            docs.mkdir()
            runpod_key = "RUNPOD" + "_API_KEY"
            placeholder_key = "API" + "_KEY"
            aws_key = "AWS_SECRET" + "_ACCESS_KEY"
            signature_param = "X-Amz-" + "Signature=redacted"
            (docs / "ok.md").write_text(
                f"{runpod_key}=${{{runpod_key}}}\n"
                f"{placeholder_key}={{{{ RUNPOD_SECRET_demo_api_key }}}}\n"
                f"{aws_key}=<runpod-s3-secret-key>\n"
                f"https://s3.amazonaws.com/bucket/object?{signature_param}\n"
                "password=example-password\n"
            )

            hits = scan_for_sensitive_text(base)

        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
