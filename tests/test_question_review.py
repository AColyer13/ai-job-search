import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "job_scraper"))
import apply_answers as aa
import question_review as qr
from apply_forms import Field


def test_a_near_duplicate_is_saved_and_junk_is_not(tmp_path, monkeypatch):
    day = "2026-11-01"
    close = "Are you legally authorized to perform work in the United States?"
    issues = tmp_path / "issues.jsonl"
    rows = [
        {"ts": f"{day}T15:00:00", "kind": "asked", "label": "section_1793_question_3"},
        {"ts": f"{day}T15:01:00", "kind": "asked", "label": "What is your favorite color?"},
        {"ts": f"{day}T15:02:00", "kind": "asked", "label": close},
        {"ts": f"{day}T15:03:00", "kind": "asked", "label": "Are you legally authorized to work in the United States?"},
        {"ts": "2026-10-30T15:00:00", "kind": "asked", "label": "Do you own a boat?"},
    ]
    issues.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    learned = tmp_path / "learned.json"
    report_path = tmp_path / "report.json"
    before = aa.resolve(Field(kind="radio", label=close, key=aa.normalize_label(close), required=True, options=["Yes", "No"]), aa.DEFAULT_PROFILE, {})
    assert before[0] == "ask"
    report = qr.review(day, issues_path=issues, learned_path=learned, report_path=report_path, profile=aa.DEFAULT_PROFILE)
    assert "section_1793_question_3" in report["ignored"]
    assert "What is your favorite color?" in report["needs_a_fact"]
    assert close in report["added"]
    assert "Are you legally authorized to work in the United States?" in report["already_answered"]
    assert "Do you own a boat?" not in report["added"]
    monkeypatch.setattr(aa, "LEARNED_PATH", learned)
    after = aa.resolve(Field(kind="radio", label=close, key=aa.normalize_label(close), required=True, options=["Yes", "No"]), aa.DEFAULT_PROFILE, {})
    assert after[:2] == ("fill", "Yes")
