"""The audit that checks the state file, the applied log and the tracker agree."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import tracking_audit  # noqa: E402

URL = "https://job-boards.greenhouse.io/acme/jobs/123"


def entry(**kw):
    base = {"url": URL, "title": "Engineer", "company": "Acme", "status": "applied", "applied_by": "AI",
            "resume": "resume.pdf"}
    return {"k": {**base, **kw}}


def job(**kw):
    return {"j1": {"url": URL, "title": "Engineer", "company": "Acme", "status": "applied", **kw}}


ROW = [{"company": "Acme", "role": "Engineer", "source": URL}]


def test_matching_records_report_nothing():
    assert tracking_audit.audit(entry(), job(), ROW, now=0) == []


def test_a_sent_job_missing_from_the_log_is_flagged():
    problems = tracking_audit.audit({}, job(), ROW, now=0)
    assert len(problems) == 1 and problems[0].startswith("SENT BUT NOT LOGGED")


def test_an_applied_elsewhere_job_is_not_a_missing_log_entry():
    assert tracking_audit.audit({}, job(note="applied elsewhere"), ROW, now=0) == []


def test_an_applied_job_missing_from_the_tracker_is_flagged():
    problems = tracking_audit.audit(entry(), job(), [], now=0)
    assert [p.split(":")[0] for p in problems] == ["NOT IN TRACKER"]


def test_an_ai_application_without_a_resume_is_flagged_but_adams_is_not():
    assert tracking_audit.audit(entry(resume=""), job(), ROW, now=0)[0].startswith("NO RESUME RECORDED")
    assert tracking_audit.audit(entry(resume="", applied_by="Adam"), job(), ROW, now=0) == []


def test_a_pending_log_write_is_flagged():
    problems = tracking_audit.audit(entry(), job(unlogged={"status": "applied", "kw": {}}), ROW, now=0)
    assert [p.split(":")[0] for p in problems] == ["LOG WRITE PENDING"]


def test_a_chrome_window_waiting_too_long_is_stuck_but_a_fresh_one_is_not():
    waiting = job(status="assisting", assist_started=0)
    assert tracking_audit.audit({}, waiting, [], now=10 * 60) == []
    assert tracking_audit.audit({}, waiting, [], now=40 * 60)[0].startswith("STUCK")


def test_a_closed_window_handoff_says_to_verify_by_email():
    handed = job(status="handed_off", reason="The Chrome window was closed before the application was sent.")
    assert tracking_audit.audit({}, handed, [], now=0)[0].startswith("VERIFY BY EMAIL")
    plain = job(status="handed_off", reason="Needs a work authorization answer")
    assert tracking_audit.audit({}, plain, [], now=0) == []
