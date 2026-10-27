"""Tracker drift: a missing CSV must not crash, and company-only rows must not hide a role."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_tracker_drift",
    Path(__file__).resolve().parent.parent / "tools" / "check_tracker_drift.py",
)
drift = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drift)


class TrackerDriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cv = root / "cv"
        self.cover = root / "cover"
        self.archive = root / "archive"
        self.cv.mkdir()
        self.cover.mkdir()
        self.archive.mkdir()
        drift.CV_DIR = self.cv
        drift.COVER_DIR = self.cover
        drift.ARCHIVE_DIR = self.archive
        drift.TRACKER_PATH = root / "job_search_tracker.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_tracker(self, rows):
        with drift.TRACKER_PATH.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("company", "role", "cv_file", "cover_letter_file"),
            )
            writer.writeheader()
            writer.writerows(rows)

    def test_missing_tracker_does_not_crash(self):
        (self.cv / "main_acme_sales_engineer.tex").write_text("% cv\n", encoding="utf-8")
        missing, rows = drift.find_drift()
        self.assertEqual(rows, 0)
        self.assertEqual([item["slug"] for item in missing], ["acme_sales_engineer"])

    def test_company_only_does_not_mask_a_different_role(self):
        (self.cv / "main_acme_sales_engineer.tex").write_text("% cv\n", encoding="utf-8")
        (self.cv / "main_acme_solutions_consultant.tex").write_text("% cv\n", encoding="utf-8")
        self._write_tracker([{
            "company": "Acme",
            "role": "Sales Engineer",
            "cv_file": "",
            "cover_letter_file": "",
        }])
        missing, rows = drift.find_drift()
        self.assertEqual(rows, 1)
        self.assertEqual([item["slug"] for item in missing], ["acme_solutions_consultant"])

    def test_exact_company_and_role_is_not_drift(self):
        (self.cv / "main_acme_sales_engineer.tex").write_text("% cv\n", encoding="utf-8")
        self._write_tracker([{
            "company": "Acme",
            "role": "Sales Engineer",
            "cv_file": "",
            "cover_letter_file": "",
        }])
        missing, rows = drift.find_drift()
        self.assertEqual(rows, 1)
        self.assertEqual(missing, [])

    def test_cv_file_column_matches_the_slug(self):
        (self.cv / "main_acme_sales_engineer.tex").write_text("% cv\n", encoding="utf-8")
        self._write_tracker([{
            "company": "Other",
            "role": "Other",
            "cv_file": "cv/main_acme_sales_engineer.tex",
            "cover_letter_file": "",
        }])
        missing, _rows = drift.find_drift()
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
