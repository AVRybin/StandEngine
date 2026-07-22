import datetime as dt
import unittest
from pathlib import Path

import yaml


class TrivyIgnorePolicyTest(unittest.TestCase):
    def test_ignores_are_scoped_documented_and_short_lived(self):
        ignore_file = Path(__file__).resolve().parents[1] / ".trivyignore.yaml"
        config = yaml.safe_load(ignore_file.read_text(encoding="utf-8"))
        today = dt.date.today()
        latest_allowed_expiry = today + dt.timedelta(days=31)

        entries = [
            entry
            for section in config.values()
            for entry in section
        ]

        for entry in entries:
            with self.subTest(finding=entry.get("id")):
                self.assertTrue(entry.get("id"), "Each ignore needs a finding ID")
                self.assertTrue(
                    entry.get("statement"),
                    "Each ignore needs a remediation statement",
                )
                self.assertTrue(
                    entry.get("paths"),
                    "Each ignore must be scoped to explicit image paths",
                )

                expiry = entry.get("expired_at")
                self.assertIsInstance(
                    expiry,
                    dt.date,
                    "expired_at must use the unquoted YYYY-MM-DD YAML date format",
                )
                self.assertGreaterEqual(expiry, today, "Trivy ignore has expired")
                self.assertLessEqual(
                    expiry,
                    latest_allowed_expiry,
                    "Trivy ignores may be granted for at most 31 days",
                )


if __name__ == "__main__":
    unittest.main()
