import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scrape_ofbiz_hr import issue_to_row


ROOT = Path(__file__).parent


class CsvFixtureTest(unittest.TestCase):
    def test_csv_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ofbiz_hr_tasks.csv"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scrape_ofbiz_hr.py"), "--input-csv",
                 str(ROOT / "fixtures" / "ofbiz_hr_sample.csv"), "--output", str(output)],
                text=True, capture_output=True, check=True,
            )
            self.assertIn("Exported 2 issues; skipped 1 rows; filtered out 0; duplicates 1; missing assignees 1.", result.stdout)
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0]), ["Status", "Priority", "Created", "Resolved", "Labels", "Description", "title", "task_type", "assignee_id", "lead_time_days"])
        self.assertEqual(rows[0]["Status"], "Done")
        self.assertEqual(rows[0]["lead_time_days"], "6")
        self.assertEqual(rows[0]["Description"], "Leave form, with comma and a second line")
        self.assertEqual(rows[1]["lead_time_days"], "0")
        self.assertEqual(rows[1]["assignee_id"], "")

    def test_task_created_outside_2024_to_today_window_is_skipped(self):
        for created in ("2023-12-31", (date.today() + timedelta(days=1)).isoformat()):
            row, _ = issue_to_row({
                "fields": {
                    "created": created,
                    "resolutiondate": created,
                    "resolution": {"name": "Fixed"},
                }
            })
            self.assertIsNone(row)
