import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from cloud_bridge.cli import main


class ProvidersCliOutputTests(unittest.TestCase):
    def test_registered_providers_show_launch_support(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["providers"]), 0)
        rows = {line.split()[0]: line for line in output.getvalue().splitlines()}

        for provider in ("runpod", "huggingface"):
            with self.subTest(provider=provider):
                self.assertIn("[automated]", rows[provider])
                self.assertNotIn("[setup-guidance]", rows[provider])
        for provider in ("modal", "nvidia-nim"):
            with self.subTest(provider=provider):
                self.assertIn("[setup-guidance]", rows[provider])
                self.assertNotIn("[automated]", rows[provider])
        self.assertNotIn("slot:", output.getvalue())

    def test_launch_support_label_does_not_depend_on_provenance(self):
        for automated in (True, False):
            for provenance in ("", "researched", "exercised outside this bridge"):
                with self.subTest(automated=automated, provenance=provenance):
                    row = {
                        "provider": "example",
                        "adapter": "example_v1",
                        "automated_launch": automated,
                        "implemented": automated,
                        "provenance": provenance,
                        "summary": "Example provider.",
                    }
                    adapter = Mock()
                    adapter.status.return_value = row
                    output = io.StringIO()
                    with patch("cloud_bridge.cli.available_adapters", return_value=[adapter]):
                        with redirect_stdout(output):
                            self.assertEqual(main(["providers"]), 0)
                    expected = "[automated]" if automated else "[setup-guidance]"
                    self.assertEqual(output.getvalue().split()[1], expected)

    def test_json_retains_support_and_provenance(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["providers", "--json"]), 0)
        rows = {row["provider"]: row for row in json.loads(output.getvalue())}
        self.assertTrue(rows["runpod"]["automated_launch"])
        self.assertFalse(rows["modal"]["automated_launch"])
        self.assertTrue(rows["runpod"]["provenance"])
        self.assertIn("provenance", rows["modal"])


if __name__ == "__main__":
    unittest.main()
