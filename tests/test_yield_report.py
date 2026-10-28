"""The yield report grades only applications old enough to have answered, and says when n is too small."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import yield_report  # noqa: E402

TODAY = date(2026, 11, 20)


def row(day, status="applied", **over):
    base = {"date": day, "status": status, "score": "", "discovery_source": "himalayas.app",
            "apply_mode": "ai", "resume_version": "r1", "role_type": "sales engineering", "channel": "ashby form"}
    base.update(over)
    return base


def test_wilson_interval_is_wide_for_small_n_and_empty_for_none():
    low, high = yield_report.wilson(0, 5)
    assert low == 0 and high > 0.4
    assert yield_report.wilson(0, 0) == (0.0, 0.0)
    low, high = yield_report.wilson(10, 100)
    assert 0.05 < low < 0.10 < high < 0.18


def test_applications_under_21_days_old_are_not_graded():
    matured, too_new, undated = yield_report.summarize(
        [row("2026-10-01"), row("2026-11-10"), row("not a date"), row("2026-10-01", "withdrawn")], TODAY
    )
    assert (len(matured), len(too_new), len(undated)) == (1, 1, 1)


def test_positive_replies_count_but_a_rejection_does_not():
    rows = [row("2026-10-01", "interview"), row("2026-10-01", "rejected"), row("2026-10-01"), row("2026-10-01", "no response")]
    text = yield_report.build_report(rows, TODAY)
    line = next(l for l in text.splitlines() if l.strip().startswith("himalayas.app"))
    assert line.split()[1:4] == ["4", "1", "1"]


def test_score_zero_is_its_own_bucket_and_blank_is_unknown():
    assert yield_report.score_bucket({"score": "0"}) == "0 (Low gate)"
    assert yield_report.score_bucket({"score": ""}) == "score unknown"
    assert yield_report.score_bucket({"score": "86"}) == "70+ (High)"


def test_small_data_says_not_to_decide():
    text = yield_report.build_report([row("2026-10-01", "interview")] + [row("2026-10-01")] * 5, TODAY)
    assert "thin: read as noise" in text and "Not enough to decide anything" in text


def test_old_tracker_without_yield_columns_still_reports():
    old = {"date": "2026-10-01", "status": "applied", "role_type": "qa", "channel": "greenhouse form"}
    assert "unknown" in yield_report.build_report([old], TODAY)
