import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cloud_bridge import learnings
from cloud_bridge.cli import main as cli_main
from cloud_bridge.util import redact, redact_text

# Build the forbidden/secret tokens by concatenation so the literals never appear
# in this file's source - tests/ is scanned by the public-readiness audit.
USERS = "/" + "Users/"
AKIA_SAMPLE = "AKIA" + "IOSFODNN7EXAMPLE"


class ScrubFindingsTests(unittest.TestCase):
    def test_clean_text_has_no_findings(self):
        self.assertEqual(learnings.scrub_findings("modal billing tag empty", "use --tag-names"), [])

    def test_internal_token_flagged(self):
        self.assertIn("internal_token", learnings.scrub_findings(f"pod failed on {USERS}someone/repo"))

    def test_secret_flagged(self):
        # AKIA-prefixed value matches the high-confidence secret regex
        self.assertIn("high_confidence_secret", learnings.scrub_findings(f"key was {AKIA_SAMPLE}"))

    def test_findings_never_echo_the_value(self):
        findings = learnings.scrub_findings("ghp_" + "a" * 30)
        self.assertEqual(findings, ["high_confidence_secret"])
        self.assertNotIn("ghp_", "".join(findings))

    def test_reuses_full_audit_detectors(self):
        # These were MISSED before scrub reused public_readiness.sensitive_line_hits.
        # Built by concatenation so the literals never appear in this scanned file.
        aws_payload = "aws_secret_access_key" + "=wJalr" + "XUtnFEMIbPxRfiCYEXAMPLEKEY0"
        presigned = "https://b.s3/k?X-Amz-Signature" + "=0a1b2c3d4e5f6a7b8c9d"
        pw_payload = "db " + "password" + "=hunter2-prod-value"
        self.assertIn("secret_assignment", learnings.scrub_findings(aws_payload))
        self.assertIn("presigned_url", learnings.scrub_findings(presigned))
        self.assertIn("secret_assignment", learnings.scrub_findings(pw_payload))

    def test_secret_split_across_fields_still_caught(self):
        # bearer token split across two fields - the space-joined concat catches it
        findings = learnings.scrub_findings("Bearer", "abcdef0123456789ABCDEF")
        self.assertIn("bearer_token", findings)


class LedgerRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_read(self):
        entry = learnings.record_learning(
            provider="modal",
            symptom="billing --tag returns empty",
            category="billing",
            severity="warn",
            status="resolved",
            resolution="use billing report --tag-names",
            tags=["billing", "cli"],
            path=self.path,
        )
        self.assertEqual(entry["event"], "learning")
        self.assertEqual(entry["tags"], ["billing", "cli"])
        self.assertNotIn("scrub_warning", entry)
        entries = learnings.read_entries(self.path)
        self.assertEqual(len(learnings.learnings(entries)), 1)
        self.assertEqual(entries[0]["id"], entry["id"])

    def test_append_only(self):
        learnings.record_learning(provider="a", symptom="one", path=self.path)
        learnings.record_learning(provider="b", symptom="two", path=self.path)
        self.assertEqual(len(learnings.read_entries(self.path)), 2)

    def test_scrub_warning_recorded(self):
        entry = learnings.record_learning(
            provider="runpod",
            symptom=f"failed on {USERS}x/secret",
            path=self.path,
        )
        self.assertIn("internal_token", entry.get("scrub_warning", []))

    def test_read_missing_file_is_empty(self):
        self.assertEqual(learnings.read_entries(Path(self.tmp.name) / "nope.jsonl"), [])

    def test_malformed_line_ignored(self):
        self.path.write_text('{"event":"learning","id":"x"}\nnot json\n', encoding="utf-8")
        self.assertEqual(len(learnings.read_entries(self.path)), 1)


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _entries(self):
        return learnings.read_entries(self.path)

    def test_candidates_require_resolved_clean_unpromoted(self):
        clean = learnings.record_learning(
            provider="modal", symptom="x", status="resolved", resolution="do y", path=self.path
        )
        learnings.record_learning(provider="modal", symptom="open one", status="open", path=self.path)
        learnings.record_learning(
            provider="runpod", symptom=f"bad {USERS}z", status="resolved", resolution="fix", path=self.path
        )
        candidates = learnings.promotion_candidates(self._entries())
        ids = {c["id"] for c in candidates}
        self.assertEqual(ids, {clean["id"]})

    def test_mark_promoted_removes_from_candidates(self):
        clean = learnings.record_learning(
            provider="modal", symptom="x", status="resolved", resolution="do y", path=self.path
        )
        self.assertEqual(len(learnings.promotion_candidates(self._entries())), 1)
        learnings.mark_promoted(clean["id"], path=self.path)
        self.assertEqual(learnings.promotion_candidates(self._entries()), [])

    def test_promotion_bullet_format(self):
        record = {"symptom": "billing --tag empty.", "resolution": "use --tag-names."}
        self.assertEqual(learnings.promotion_bullet(record), "billing --tag empty -> use --tag-names")


class SearchAndStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        learnings.record_learning(
            provider="modal", symptom="billing tag empty", category="billing",
            status="resolved", resolution="use --tag-names", tags=["cli"], path=self.path,
        )
        learnings.record_learning(
            provider="lambda", symptom="filesystem still billing", category="cleanup",
            severity="critical", status="open", path=self.path,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_filter_by_provider(self):
        out = learnings.search(learnings.read_entries(self.path), provider="lambda")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["provider"], "lambda")

    def test_filter_by_query(self):
        out = learnings.search(learnings.read_entries(self.path), query="tag-names")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["provider"], "modal")

    def test_filter_by_status_and_tag(self):
        self.assertEqual(len(learnings.search(learnings.read_entries(self.path), status="open")), 1)
        self.assertEqual(len(learnings.search(learnings.read_entries(self.path), tag="cli")), 1)

    def test_stats(self):
        report = learnings.stats(learnings.read_entries(self.path))
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["open"], 1)
        self.assertEqual(report["resolved"], 1)
        self.assertEqual(report["by_provider"], {"lambda": 1, "modal": 1})


class ResearchBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        learnings.record_learning(
            provider="modal", symptom="billing tag empty", status="resolved",
            resolution="use --tag-names", path=self.path,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_brief_includes_prior_and_known_patterns(self):
        adapter_status = {
            "known_patterns": ["blocking fn.remote() is the only proven invocation"],
            "learnings_doc": "docs/providers/modal.md",
            "docs": {"a": "https://example.com/a"},
        }
        brief = learnings.build_research_brief(
            provider="modal",
            symptom="billing tag empty",
            entries=learnings.read_entries(self.path),
            adapter_status=adapter_status,
        )
        self.assertEqual(len(brief["prior_learnings"]), 1)
        self.assertEqual(brief["provider_known_patterns"], adapter_status["known_patterns"])
        self.assertEqual(brief["doc_links"], ["https://example.com/a"])
        self.assertTrue(any("modal" in q for q in brief["suggested_search_queries"]))
        self.assertIn("research sub-agent", brief["agent_instruction"])
        self.assertIn("--provider modal", brief["record_resolution_command"])

    def test_brief_carries_failing_invocation_and_context(self):
        learnings.record_learning(
            provider="modal", symptom="billing tag empty", status="resolved",
            resolution="use --tag-names", context="seen during canary", path=self.path,
        )
        brief = learnings.build_research_brief(
            provider="modal", symptom="billing tag empty",
            entries=learnings.read_entries(self.path), failing_invocation="modal billing --tag x",
        )
        self.assertEqual(brief["failing_invocation"], "modal billing --tag x")
        self.assertTrue(any("context" in r for r in brief["prior_learnings"]))

    def test_brief_tolerates_missing_adapter(self):
        brief = learnings.build_research_brief(
            provider="unknownthing", symptom="boom", entries=[], adapter_status=None
        )
        self.assertEqual(brief["provider_known_patterns"], [])
        self.assertEqual(brief["doc_links"], [])


class DefaultLedgerLocationTests(unittest.TestCase):
    def test_default_dir_is_gitignored_internal_path(self):
        prior = os.environ.pop(learnings.LEDGER_DIR_ENV, None)
        try:
            path = learnings.ledger_path()
        finally:
            if prior is not None:
                os.environ[learnings.LEDGER_DIR_ENV] = prior
        # internal/private/ is gitignored, so the runtime ledger never reaches the public audit
        self.assertEqual(path.parent.parent.name, "private")
        self.assertEqual(path.parent.parent.parent.name, "internal")

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[learnings.LEDGER_DIR_ENV] = tmp
            try:
                self.assertEqual(learnings.ledger_dir(), Path(tmp))
            finally:
                os.environ.pop(learnings.LEDGER_DIR_ENV, None)


class LearningsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ[learnings.LEDGER_DIR_ENV] = self.tmp.name

    def tearDown(self):
        os.environ.pop(learnings.LEDGER_DIR_ENV, None)
        self.tmp.cleanup()

    def _run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main(list(argv))
        return code, buf.getvalue()

    def test_record_then_list_json(self):
        code, _ = self._run(
            "learnings", "record", "--provider", "modal",
            "--symptom", "billing tag empty", "--status", "resolved",
            "--resolution", "use --tag-names",
        )
        self.assertEqual(code, 0)
        code, out = self._run("learnings", "list", "--json")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "modal")

    def test_brief_pulls_provider_entry_knowledge(self):
        code, out = self._run("learnings", "brief", "--provider", "modal", "--symptom", "x", "--json")
        self.assertEqual(code, 0)
        brief = json.loads(out)
        self.assertTrue(brief["provider_known_patterns"])  # modal provider entry has known_patterns
        self.assertEqual(brief["learnings_doc"], "docs/providers/modal.md")

    def test_promote_excludes_scrub_warned(self):
        self._run("learnings", "record", "--provider", "runpod", "--symptom", f"bad {USERS}x", "--status", "resolved", "--resolution", "fix")
        code, out = self._run("learnings", "promote", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_renderers_tolerate_malformed_entry(self):
        # a valid-JSON learning missing id/symptom/provider must not crash list/promote
        ledger = Path(self.tmp.name) / "ledger.jsonl"
        ledger.write_text('{"event":"learning","ts":"t","status":"resolved"}\n', encoding="utf-8")
        self.assertEqual(self._run("learnings", "list")[0], 0)
        self.assertEqual(self._run("learnings", "promote")[0], 0)

    def test_limit_negative_is_clamped(self):
        self._run("learnings", "record", "--provider", "a", "--symptom", "one")
        self._run("learnings", "record", "--provider", "b", "--symptom", "two")
        code, out = self._run("learnings", "list", "--limit", "-1", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 0)  # clamped to 0, not "all but the last"


class CliRobustnessTests(unittest.TestCase):
    def test_bin_wrapper_exists_and_executable(self):
        wrapper = Path(__file__).resolve().parents[1] / "bin" / "cloud-bridge"
        self.assertTrue(wrapper.is_file(), "bin/cloud-bridge wrapper must exist")
        self.assertTrue(os.access(wrapper, os.X_OK), "bin/cloud-bridge must be executable")

    def test_version_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            cli_main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("cloud-bridge", buf.getvalue())

    def test_help_usage_uses_cloud_bridge(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            cli_main(["--help"])
        self.assertIn("cloud-bridge", buf.getvalue())
        self.assertNotIn("runpod-bridge", buf.getvalue())

    def test_missing_manifest_clean_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["validate-manifest", missing])
            self.assertEqual(code, 1)  # clean exit, not a traceback


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_unique_for_same_symptom_rehit(self):
        a = learnings.record_learning(provider="modal", symptom="same", resolution="fix A", status="resolved", path=self.path)
        b = learnings.record_learning(provider="modal", symptom="same", resolution="fix B", status="resolved", path=self.path)
        self.assertNotEqual(a["id"], b["id"])
        # both survive in the promotion queue (neither evicts the other)
        ids = {c["id"] for c in learnings.promotion_candidates(self._entries())}
        self.assertEqual(ids, {a["id"], b["id"]})

    def test_concurrent_appends_have_unique_ids(self):
        import threading

        def rec():
            learnings.record_learning(
                provider="modal", symptom="same", resolution="same fix", status="resolved", path=self.path
            )

        threads = [threading.Thread(target=rec) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        entries = learnings.learnings(learnings.read_entries(self.path))
        self.assertEqual(len(entries), 40)  # flock serialized writes - no torn/dropped lines
        self.assertEqual(len({e["id"] for e in entries}), 40)  # no id collisions under contention

    def _entries(self):
        return learnings.read_entries(self.path)

    def test_mark_promoted_refuses_non_candidate(self):
        with self.assertRaises(ValueError):
            learnings.mark_promoted("deadbeef0000", path=self.path)
        rec = learnings.record_learning(provider="x", symptom="y", status="open", path=self.path)
        with self.assertRaises(ValueError):
            learnings.mark_promoted(rec["id"], path=self.path)  # open is not promotable

    def test_corrupt_lines_counted(self):
        self.path.write_text('{"event":"learning","id":"x"}\nbroken\n{bad}\n', encoding="utf-8")
        self.assertEqual(learnings.corrupt_lines(self.path), 2)

    def test_stats_reports_promotable(self):
        learnings.record_learning(provider="m", symptom="s", status="resolved", resolution="r", path=self.path)
        self.assertEqual(learnings.stats(self._entries())["promotable"], 1)


class LedgerSafetyTests(unittest.TestCase):
    def test_warns_when_dir_inside_repo_and_not_ignored(self):
        os.environ[learnings.LEDGER_DIR_ENV] = str(learnings._repo_root() / "docs" / "learnings")
        try:
            self.assertIn("not a gitignored path", learnings.ledger_safety_warning())
        finally:
            os.environ.pop(learnings.LEDGER_DIR_ENV, None)

    def test_no_warning_for_external_or_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[learnings.LEDGER_DIR_ENV] = tmp
            try:
                self.assertEqual(learnings.ledger_safety_warning(), "")
            finally:
                os.environ.pop(learnings.LEDGER_DIR_ENV, None)
        prior = os.environ.pop(learnings.LEDGER_DIR_ENV, None)
        try:
            self.assertEqual(learnings.ledger_safety_warning(), "")  # default internal/private/
        finally:
            if prior is not None:
                os.environ[learnings.LEDGER_DIR_ENV] = prior


class RedactionTests(unittest.TestCase):
    def test_value_aware_redact_under_benign_key(self):
        # the C1 fix: a secret under a non-sensitive key name is still redacted
        out = redact({"MY_DB_URL": "postgres://u:" + "fakepw123456" + "@host/db"})
        self.assertIn("<redacted>", out["MY_DB_URL"])
        self.assertNotIn("fakepw123456", out["MY_DB_URL"])
        self.assertIn("host/db", out["MY_DB_URL"])  # structure preserved

    def test_redact_text_presigned_and_jwt(self):
        presigned = "https://b.s3/k?" + "X-Amz-Signature" + "=" + "abcdef0123456789xyz"
        self.assertIn("<redacted>", redact_text(presigned))
        self.assertNotIn("abcdef0123456789xyz", redact_text(presigned))
        self.assertEqual(redact_text("eyJ" + "aaa.eyJbbb.cccddd"), "<redacted>")

    def test_redact_preserves_sha256(self):
        digest = "a" * 64
        self.assertEqual(redact_text(digest), digest)

    def test_redact_cycle_safe(self):
        d: dict = {}
        d["self"] = d
        redact(d)  # must not infinitely recurse


if __name__ == "__main__":
    unittest.main()
