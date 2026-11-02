"""The first-value command must work without browser, Telegram or model setup."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobs


def configure(tmp_path, monkeypatch, store):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setattr(jobs.daily_digest, "STORE_PATH", path)
    monkeypatch.setattr(jobs.applied_log, "LOG_PATH", tmp_path / "applied.json")


def test_first_value_includes_a_match_and_its_posting_link(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {"jobs": {"one": {
        "title": "Sales Engineer", "company": "Example", "fit": "high", "location": "Remote",
        "url": "https://example.com/jobs/123", "body_status": "opened",
        "description": "Build demos and help customers.",
    }}})
    assert jobs.main() == 0
    text = capsys.readouterr().out
    assert "Sales Engineer at Example" in text and "https://example.com/jobs/123" in text
    assert "Not attempted yet" in text


def test_empty_first_run_explains_how_to_get_results(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {})
    assert jobs.main() == 0
    assert "To search now" in capsys.readouterr().out


def test_damaged_log_does_not_mislabel_jobs_as_unattempted(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {})
    jobs.applied_log.LOG_PATH.write_text("{broken", encoding="utf-8")
    assert jobs.main() == 1
    assert "Restore" in capsys.readouterr().out


def card(company="Example", **changes):
    return {"title": "Sales Engineer", "company": company, "fit": "high", "location": "Remote",
            "url": f"https://example.com/jobs/{company}", "body_status": "opened",
            "description": "Build demos and help customers.", **changes}


@pytest.mark.parametrize("link", [None, "", " ", "/jobs/123", "javascript:alert(1)", "file:///tmp/job",
                                   "https://", "https://example.com:bad/job", "https://user:pass@example.com/job",
                                   "https://example.com/a\nb", "https://[", 123])
def test_unusable_link_is_an_explicit_failure_not_a_blank_action(tmp_path, monkeypatch, capsys, link):
    configure(tmp_path, monkeypatch, {"jobs": {"one": card(url=link)}})
    assert jobs.main() == 1
    text = capsys.readouterr().out
    assert "Posting link unavailable" in text and "No usable posting links" in text
    assert "Not attempted yet" not in text


def test_invalid_employer_link_falls_back_to_valid_original(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {"jobs": {"one": card(apply_url=123)}})
    assert jobs.main() == 0
    assert "https://example.com/jobs/Example" in capsys.readouterr().out


def test_linkless_jobs_do_not_displace_usable_matches(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {"jobs": {"bad": card("Broken", url=None, fit_score=100),
                                               "good": card("Usable", fit_score=50)}})
    assert jobs.main() == 0
    text = capsys.readouterr().out
    assert "Posting link unavailable for 1" in text
    assert "Sales Engineer at Usable" in text and "Sales Engineer at Broken" not in text


@pytest.mark.parametrize("status", ["applied", "attempted", "recommended", "unsent"])
def test_unattempted_medium_fit_precedes_handled_high_fit(tmp_path, monkeypatch, capsys, status):
    old, fresh = card("Handled", fit_score=100), card("Fresh", fit="medium", fit_score=60)
    configure(tmp_path, monkeypatch, {"jobs": {"old": old, "fresh": fresh}})
    entry = {**old, "status": status, "reason": "Login required", "applied_by": "AI"}
    jobs.applied_log.LOG_PATH.write_text(json.dumps({"entries": {
        jobs.daily_digest._key(old["url"]): entry}}), encoding="utf-8")
    assert jobs.main() == 0
    text = capsys.readouterr().out
    assert text.index("at Fresh") < text.index("at Handled")
    assert text.index("Unattempted jobs:") < text.index("Previously handled:")


def test_handled_job_with_another_posting_url_stays_in_history(tmp_path, monkeypatch, capsys):
    old, fresh = card("Handled"), card("Fresh", fit="medium")
    configure(tmp_path, monkeypatch, {"jobs": {"old": old, "fresh": fresh}})
    jobs.applied_log.LOG_PATH.write_text(json.dumps({"entries": {"old-url": {
        **old, "url": "https://other.example/123", "status": "applied", "applied_by": "AI"}}}), encoding="utf-8")
    assert jobs.main() == 0
    text = capsys.readouterr().out
    assert text.index("at Fresh") < text.index("at Handled") and "AI applied" in text


def test_unattempted_jobs_keep_fit_score_order(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, {"jobs": {
        "a": card("Lower", fit_score=70), "b": card("Higher", fit_score=95)}})
    assert jobs.main() == 0
    text = capsys.readouterr().out
    assert text.index("at Higher") < text.index("at Lower")
