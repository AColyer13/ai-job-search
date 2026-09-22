"""Unit tests for job_scraper/pipeline.py - no network access."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import pipeline  # noqa: E402


# --- fit() -----------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Junior Software Engineer", "high"),
    ("Entry Level Full Stack Developer", "high"),
    ("Junior AI Engineer", "high"),
    ("AI Application Engineer", "high"),
    ("Solutions Engineer", "high"),
    ("Sales Engineer", "high"),
    ("Technical Account Manager", "high"),
    ("Senior Solutions Engineer", "medium"),
    ("AI Engineer", "medium"),
    ("Software Engineer", "medium"),
    ("Senior Software Engineer", "low"),
    ("Staff AI Engineer", "low"),
    ("Software Engineering Intern", "low"),
    ("Director of Solutions Engineering", "low"),
    ("Embedded Firmware Engineer", "low"),
    ("Senior Software Engineer 2027", "low"),
])
def test_fit(title, expected):
    assert pipeline.fit(title) == expected


def test_fit_skills_fallback():
    assert pipeline.fit("Product Engineer", ["react", "typescript"]) == "medium"
    assert pipeline.fit("Product Engineer", ["cobol"]) == "low"


# --- loc_tier() ------------------------------------------------------------

@pytest.mark.parametrize("loc,wm,expected", [
    ("Edina, MN", None, "mn"),
    ("Minneapolis, MN", None, "mn"),
    ("Chanhassen, MN", None, "mn"),
    ("St. Paul, MN", None, "exclude-east"),
    ("Gulfport, Mississippi", None, "ms"),
    ("Jackson, MS", None, "ms-inland"),
    ("Remote", None, "remote"),
    ("United States", "remote", "remote"),
    ("Austin, TX", "remote", "remote"),
    ("Berlin, Germany", None, "exclude-intl"),
    ("Toronto, Canada", "remote", "exclude-intl"),
    ("Chicago, IL", None, "us-reloc"),
    ("Dayton, OH", None, "unknown"),
])
def test_loc_tier(loc, wm, expected):
    assert pipeline.loc_tier(loc, wm) == expected


# --- title filters ----------------------------------------------------------

@pytest.mark.parametrize("title,keep", [
    ("Junior Software Engineer", True),
    ("AI Engineer", True),
    ("Fullstack Entwickler (m/w/d)", False),
    ("Desarrollador Full Stack", False),
    ("Ingeniero de Inteligencia Artificial", False),
    ("Barista", False),
])
def test_keep_title(title, keep):
    assert pipeline._keep_title(title) == keep


# --- expire -----------------------------------------------------------------

def test_expire_marks_only_stale_new(tmp_path, monkeypatch):
    seen = {
        "a": {"status": "new", "first_seen": "2026-08-01"},
        "b": {"status": "new", "first_seen": "2999-01-01"},
        "c": {"status": "ranked", "first_seen": "2026-08-01"},
        "d": {"status": "new"},  # no first_seen -> treated as far future, kept
    }
    path = tmp_path / "seen_jobs.json"
    path.write_text(json.dumps({"seen": seen}), encoding="utf-8")
    monkeypatch.setattr(pipeline, "SEEN_PATH", path)

    args = type("A", (), {"days": 21, "dry_run": False})()
    assert pipeline.cmd_expire(args) == 0

    out = json.loads(path.read_text(encoding="utf-8"))["seen"]
    assert out["a"]["status"] == "expired"
    assert out["b"]["status"] == "new"
    assert out["c"]["status"] == "ranked"
    assert out["d"]["status"] == "new"


# --- dedup ------------------------------------------------------------------

def test_already_matches_url_id_and_company_title():
    seen = {
        "https://x.com/jobs/12345678": {
            "url": "https://x.com/jobs/12345678",
            "company": "Acme", "title": "Junior SWE",
        }
    }
    urls, ids, ct = pipeline._seen_indexes(seen)
    assert pipeline._already({"url": "https://x.com/jobs/12345678"}, urls, ids, ct)
    assert pipeline._already({"url": "https://x.com/jobs/12345678?utm_source=y"}, urls, ids, ct)
    assert pipeline._already({"url": "https://other.com/12345678"}, urls, ids, ct)
    assert pipeline._already({"url": "https://new.com/1", "company": "ACME", "title": "junior swe"}, urls, ids, ct)
    assert not pipeline._already({"url": "https://new.com/2", "company": "Beta", "title": "SWE"}, urls, ids, ct)
