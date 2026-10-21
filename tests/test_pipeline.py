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
    ("Web Developer", "medium"),
    ("Application Developer", "medium"),
    ("Junior Web Developer", "high"),
    ("Junior Application Developer", "high"),
    ("Python Developer", "medium"),
    ("Backend Developer", "medium"),
    ("Customer Engineer", "high"),
    ("Junior QA Engineer", "medium"),
    ("Junior Quality Assurance Analyst", "medium"),
    ("Software Tester", "medium"),
    ("Senior QA Engineer", "low"),
    ("Senior SDET", "low"),
    ("Implementation Engineer", "high"),
    ("Implementation Consultant", "high"),
    ("Technical Sales Representative", "high"),
    ("Technical Sales Consultant", "high"),
    ("Technical Sales Rep", "high"),
    ("Technical Sales Trainee (Minneapolis)", "high"),
    ("Senior Technical Sales Consultant", "medium"),
    ("Pre-Sales Consultant", "high"),
    ("Presales Consultant", "high"),
    ("Pre Sales Consultant", "high"),
    ("Presales Engineer", "high"),
    ("Solutions Consultant", "high"),
    ("Solutions Architect", "medium"),
    ("Senior Solutions Architect", "medium"),
    ("Associate Solutions Architect", "high"),
    ("Director, Solutions Architect", "low"),
    ("Software Architect", "low"),
    ("Cloud Architect", "low"),
])
def test_fit(title, expected):
    assert pipeline.fit(title) == expected


def test_fit_skills_fallback():
    assert pipeline.fit("Product Engineer", ["react", "typescript"]) == "medium"
    assert pipeline.fit("Product Engineer", ["cobol"]) == "low"


# --- fit() description-based demotion ---------------------------------------

def test_fit_demotes_on_years_required_in_description():
    desc = "We need someone with 6+ years of professional software experience."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "medium"
    assert pipeline.fit("Software Engineer", description=desc) == "low"


def test_fit_demotes_on_clearance_required():
    desc = "Candidates must hold an active security clearance."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "medium"


def test_fit_low_stays_low_regardless_of_description():
    desc = "6+ years of experience required. Active security clearance required."
    assert pipeline.fit("Senior Software Engineer", description=desc) == "low"


def test_fit_description_never_promotes():
    # A "2 years" mention should never push a low-fit title up a tier.
    desc = "Only 2 years of experience needed, no clearance."
    assert pipeline.fit("Embedded Firmware Engineer", description=desc) == "low"


def test_fit_no_description_unaffected():
    assert pipeline.fit("Junior Software Engineer", None, None) == "high"
    assert pipeline.fit("Junior Software Engineer", None, "") == "high"


# --- extract_salary() -------------------------------------------------------

@pytest.mark.parametrize("desc,expected", [
    ("Salary range: $80,000 - $95,000 annually.", "$80,000 - $95,000"),
    ("Pay is $70k-$90k depending on experience.", "$70k-$90k"),
    ("No compensation details provided.", None),
    (None, None),
    ("", None),
])
def test_extract_salary(desc, expected):
    assert pipeline.extract_salary(desc) == expected


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
    # Same-named city/town in a different state or province than the one
    # tiered as "mn"/"ms" - these must NOT be misclassified as local/relocatable.
    ("Long Beach, CA", None, "us-reloc"),
    ("Victoria, BC", None, "exclude-intl"),
    ("Crystal City, Virginia", None, "us-reloc"),
    ("Rogers, AR", None, "us-reloc"),
    ("Plymouth, MA", None, "us-reloc"),
    # "kiln" (MS_OK) must not match as a substring of an unrelated word.
    ("Kilnworks, TX", None, "us-reloc"),
    # The real MN/MS cities still need to keep resolving correctly.
    ("Victoria, MN", None, "mn"),
    ("Long Beach, MS", None, "ms"),
    ("Kiln, MS", None, "ms"),
])
def test_loc_tier(loc, wm, expected):
    assert pipeline.loc_tier(loc, wm) == expected


def test_presentable_list_uses_the_word_filters():
    import daily_digest

    shown, left = daily_digest.presentable_split([
        {"title": "Full Stack Developer", "fit": "high", "company": "Instient", "url": "https://example.com/instient"},
        {"title": "Sales Engineer", "fit": "medium", "company": "Lightpath", "location": "Richmond, VA, USA", "url": "https://example.com/lightpath"},
    ])
    assert [job["title"] for job in shown] == ["Sales Engineer"]
    assert left[0][1] == "engineering level"
    assert left[0][0]["company"] == "Instient"


def test_missing_command_points_at_the_daily_list(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pipeline.py"])
    code = pipeline.main()
    assert code == 2
    out = capsys.readouterr().out
    assert "daily_digest.py" in out
    assert "latest.docx" in out


def test_high_requires_a_requested_location():
    assert pipeline.location_high_block("Minneapolis, MN", None) is None
    assert pipeline.location_high_block("Eden Prairie, MN", None) is None
    assert pipeline.location_high_block("Biloxi, MS", None) is None
    assert pipeline.location_high_block("Remote", "remote") is None
    assert pipeline.location_high_block("United States", "remote") is None
    assert pipeline.location_high_block("Remote - Canada, USA", "remote") is None
    assert pipeline.location_high_block("Richmond, VA, USA", "remote")
    assert pipeline.location_high_block("Farmington, NM, United States", "remote")
    assert pipeline.location_high_block("Austin, TX", "remote")
    assert pipeline.location_high_block("Dayton, Marengo County", None)
    madison = (
        "Please note that this position is based on our campus in Madison, WI, "
        "and requires relocation to the area."
    )
    assert "Madison, WI" in pipeline.location_high_block("Biloxi, MS", None, madison)
    zones = "This role is based in Mountain, Central, or Eastern time zones."
    assert pipeline.location_high_block("United States of America", "remote", zones) is None
    job = {
        "title": "Sales Engineer",
        "fit": "high",
        "location": "Richmond, VA, USA",
        "work_mode": "remote",
    }
    pipeline.cap_high_for_location(job)
    assert job["fit"] == "medium"
    assert job["fit_before_location"] == "high"
    local = {"title": "Technical Sales Trainee", "fit": "high", "location": "Minneapolis, MN"}
    pipeline.cap_high_for_location(local)
    assert local["fit"] == "high"


# --- title filters ----------------------------------------------------------

@pytest.mark.parametrize("title,keep", [
    ("Junior Software Engineer", True),
    ("AI Engineer", True),
    ("Fullstack Entwickler (m/w/d)", False),
    ("Desarrollador Full Stack", False),
    ("Ingeniero de Inteligencia Artificial", False),
    ("Barista", False),
    ("Technical Sales Representative", True),
    ("Technical Sales Consultant", True),
    ("Pre-Sales Consultant", True),
    ("Presales Consultant", True),
    ("Solutions Architect", True),
    ("Implementation Consultant", True),
    ("Junior QA Engineer", True),
    ("Quality Assurance Analyst", True),
    ("Application Developer", True),
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
    urls, url_keys, ids, ct = pipeline._seen_indexes(seen)
    assert pipeline._already({"url": "https://x.com/jobs/12345678"}, urls, url_keys, ids, ct)
    assert pipeline._already({"url": "https://x.com/jobs/12345678?utm_source=y"}, urls, url_keys, ids, ct)
    assert pipeline._already({"url": "https://other.com/12345678"}, urls, url_keys, ids, ct)
    assert pipeline._already({"url": "https://new.com/1", "company": "ACME", "title": "junior swe"}, urls, url_keys, ids, ct)
    assert not pipeline._already({"url": "https://new.com/2", "company": "Beta", "title": "SWE"}, urls, url_keys, ids, ct)


def test_already_does_not_collide_on_shared_path_distinct_query_id():
    # Regression: HN "who is hiring" comment links all share the same path
    # (news.ycombinator.com/item) and differ only by ?id=<n>. Stripping the
    # whole query string to compare "base URLs" made every comment after the
    # first look like a duplicate of it.
    seen = {
        "https://news.ycombinator.com/item?id=111": {
            "url": "https://news.ycombinator.com/item?id=111",
            "company": "Acme", "title": "Backend Engineer",
        }
    }
    urls, url_keys, ids, ct = pipeline._seen_indexes(seen)
    assert pipeline._already(
        {"url": "https://news.ycombinator.com/item?id=111", "company": "Acme", "title": "Backend Engineer"},
        urls, url_keys, ids, ct,
    )
    assert not pipeline._already(
        {"url": "https://news.ycombinator.com/item?id=222", "company": "Beta", "title": "Frontend Engineer"},
        urls, url_keys, ids, ct,
    )


def test_update_seen_keeps_distinct_ids_and_collapses_tracking(tmp_path, monkeypatch):
    seen_path = tmp_path / "seen_jobs.json"
    seen_path.write_text(json.dumps({"seen": {}}), encoding="utf-8")
    monkeypatch.setattr(pipeline, "SEEN_PATH", seen_path)
    rd = tmp_path / "run"
    rd.mkdir()
    hn1 = "https://news.ycombinator.com/item?id=111"
    hn2 = "https://news.ycombinator.com/item?id=222"
    ad1 = "https://www.adzuna.com/land/ad/555?utm_source=x"
    ad2 = "https://www.adzuna.com/land/ad/555?utm_source=y"
    urls = [hn1, hn2, ad1, ad2]
    (rd / "sample.json").write_text(json.dumps({"results": [
        {"url": hn1, "title": "Backend Engineer", "company": "Acme"},
        {"url": hn2, "title": "Frontend Engineer", "company": "Beta"},
        {"url": ad1, "title": "SWE", "company": "Acme"},
        {"url": ad2, "title": "SWE", "company": "Acme"},
    ]}), encoding="utf-8")
    (rd / "candidates.json").write_text(json.dumps({
        "new": [{"url": u} for u in urls],
        "all": [{"url": u, "fit": "high"} for u in urls],
    }), encoding="utf-8")

    args = type("A", (), {"run_dir": str(rd)})()
    assert pipeline.cmd_update_seen(args) == 0
    stored = json.loads(seen_path.read_text(encoding="utf-8"))["seen"]
    assert hn1 in stored and hn2 in stored
    assert (ad1 in stored) ^ (ad2 in stored)


def test_already_still_collapses_tracking_param_variants():
    # Two links to the *same* posting that differ only by tracking noise
    # (utm_*, se, v) should still be recognized as duplicates.
    seen = {
        "https://www.adzuna.com/land/ad/555?se=abc&utm_medium=api&utm_source=x&v=1": {
            "url": "https://www.adzuna.com/land/ad/555?se=abc&utm_medium=api&utm_source=x&v=1",
            "company": "Acme", "title": "SWE",
        }
    }
    urls, url_keys, ids, ct = pipeline._seen_indexes(seen)
    assert pipeline._already(
        {"url": "https://www.adzuna.com/land/ad/555?se=xyz&utm_medium=api&utm_source=y&v=2"},
        urls, url_keys, ids, ct,
    )


# --- src_boards() config wiring ---------------------------------------------

def test_src_boards_skips_when_unconfigured(tmp_path, monkeypatch):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps({"cli_queries": [], "http_sources": [], "company_boards": []}), encoding="utf-8")
    monkeypatch.setattr(pipeline, "QUERIES_PATH", path)
    with pytest.raises(pipeline.SkipSource):
        pipeline.src_boards()


def test_src_boards_isolates_per_company_failures(tmp_path, monkeypatch):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps({
        "cli_queries": [], "http_sources": [],
        "company_boards": [
            {"name": "Bad Co", "platform": "greenhouse", "slug": "definitely-not-a-real-slug"},
            {"name": "Weird Co", "platform": "not-a-real-platform", "slug": "x"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline, "QUERIES_PATH", path)

    def boom(slug):
        raise RuntimeError("simulated network failure")

    monkeypatch.setitem(pipeline._BOARD_PLATFORMS, "greenhouse", boom)
    # Neither a bad slug nor an unknown platform should raise out of src_boards.
    assert pipeline.src_boards() == []


# --- expire wired into `all` ------------------------------------------------

def test_all_command_runs_expire_last(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "cmd_search", lambda args: calls.append("search") or 0)
    monkeypatch.setattr(pipeline, "cmd_process", lambda args: calls.append("process") or 0)
    monkeypatch.setattr(pipeline, "cmd_update_seen", lambda args: calls.append("update-seen") or 0)
    monkeypatch.setattr(pipeline, "cmd_expire", lambda args: calls.append("expire") or 0)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "all"])

    assert pipeline.main() == 0
    assert calls == ["search", "process", "update-seen", "expire"]
