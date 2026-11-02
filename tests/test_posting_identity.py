"""Same-title job slugs at different employers must never share requirements."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "job_scraper"))
import posting_text


def test_himalayas_requires_company_and_job_identity(monkeypatch):
    wrong = {"guid": "https://himalayas.app/companies/wrong/jobs/customer-support-engineer",
             "description": "Wrong employer. " * 30}
    right = {"guid": "https://himalayas.app/companies/right/jobs/customer-support-engineer",
             "description": "Correct employer. " * 30}
    monkeypatch.setattr(posting_text, "_get", lambda *args: json.dumps({"jobs": [wrong, right]}))
    status, text = posting_text.fetch_himalayas({"url": right["guid"], "company": "Right", "title": "Customer Support Engineer"})
    assert status == "opened" and "Correct employer" in text and "Wrong employer" not in text


def test_another_employers_matching_slug_is_not_a_readable_posting(monkeypatch):
    monkeypatch.setattr(posting_text, "_get", lambda *args: json.dumps({"jobs": [{
        "guid": "https://himalayas.app/companies/wrong/jobs/customer-support-engineer",
        "description": "Wrong employer. " * 30}]}))
    assert posting_text.fetch_himalayas({"url": "https://himalayas.app/companies/right/jobs/customer-support-engineer",
                                        "company": "Right", "title": "Customer Support Engineer"}) == ("unread", "")
