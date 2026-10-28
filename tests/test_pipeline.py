"""Unit tests for job_scraper/pipeline.py - no network access."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import pipeline  # noqa: E402


# --- fit() -----------------------------------------------------------------
# With no readable posting text the grade is low: a title cannot show whether
# the job asks for a CS degree or five years of engineering.

@pytest.mark.parametrize("title,expected", [
    ("Junior Software Engineer", "low"),
    ("Entry Level Full Stack Developer", "low"),
    ("Junior AI Engineer", "low"),
    ("Solutions Engineer", "low"),
    ("Sales Engineer", "low"),
    ("Technical Account Manager", "low"),
    ("Customer Engineer", "low"),
    ("Implementation Consultant", "low"),
    ("Technical Sales Representative", "low"),
    ("Pre-Sales Consultant", "low"),
    ("Solutions Architect", "low"),
    ("Associate Solutions Architect", "low"),
    ("Junior QA Engineer", "low"),
    ("Software Engineer", "low"),
    ("Web Developer", "low"),
    ("Senior Solutions Engineer", "low"),
    ("Senior Technical Sales Consultant", "low"),
    ("Senior Software Engineer", "low"),
    ("Staff AI Engineer", "low"),
    ("Software Engineering Intern", "low"),
    ("Director of Solutions Engineering", "low"),
    ("Embedded Firmware Engineer", "low"),
    ("Senior Software Engineer 2027", "low"),
    ("Senior QA Engineer", "low"),
    ("Director, Solutions Architect", "low"),
    ("Software Architect", "low"),
    ("Cloud Architect", "low"),
    ("Java Developer", "low"),
    ("DevOps Engineer", "low"),
])
def test_fit(title, expected):
    assert pipeline.fit(title) == expected


def test_fit_skills_fallback():
    body = _BODY + "1 year of experience."
    assert pipeline.fit("Product Engineer", ["react", "typescript"]) == "low"
    assert pipeline.fit("Product Engineer", ["react", "typescript"], body) == "medium"
    assert pipeline.fit("Product Engineer", ["cobol"], body) == "low"


# --- fit() with posting text ------------------------------------------------

_BODY = "You will run discovery calls and product demos with customers every week. " * 6
# An engineering seat is high only when the posting overlaps the stack he has shipped.
_STACK = "You will build features in Python, TypeScript and React against REST APIs and PostgreSQL. "


def test_fit_high_needs_posting_text():
    assert pipeline.fit("Junior Software Engineer", description=_BODY + _STACK + "1 year of experience.") == "high"
    assert pipeline.fit("Junior Software Engineer", description=_BODY + "1 year of experience.") == "medium"
    assert pipeline.fit("Junior Software Engineer") == "low"


def test_fit_low_when_years_exceed_resume():
    desc = _BODY + "We need someone with 6+ years of professional software experience."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "low"


def test_fit_low_when_cs_degree_required():
    desc = _BODY + "Bachelor's degree in Computer Science required."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "low"
    assert pipeline.fit("Solutions Engineer", description=desc) == "low"


def test_fit_cs_degree_with_equivalent_experience_is_fine():
    desc = _BODY + _STACK + "BS in Computer Science or equivalent experience. 1 year of experience."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "high"


def test_fit_low_when_clearance_required():
    desc = _BODY + "Candidates must hold an active security clearance."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "low"


def test_fit_sales_years_inside_resume_is_high():
    desc = _BODY + "3+ years of sales experience. Bachelor's degree."
    assert pipeline.fit("Solutions Engineer", description=desc) == "high"


def test_fit_required_five_sales_years_is_qualified():
    import scoring
    for years in (6, 7):
        job = {"title": "Sales Engineer", "description": _BODY + f"{years}+ years of sales experience required."}
        out = scoring.score(job)
        assert out["grade"] == "low" and out["disqualified"]


def test_fit_five_required_sales_years_is_a_stretch_never_high():
    import scoring
    job = {"title": "Sales Engineer", "description": _BODY + "5+ years of sales experience required."}
    out = scoring.score(job)
    assert out["grade"] == "medium" and not out["disqualified"]


def test_fit_four_required_sales_years_is_fine():
    import scoring
    job = {"title": "Sales Engineer", "description": _BODY + "4+ years of sales experience required."}
    assert scoring.score(job)["grade"] == "high"


def test_preferred_years_under_heading_do_not_disqualify():
    import scoring
    desc = _BODY + (
        "\nRequirements:\n3+ years of sales experience\n"
        "\nPreferred Qualifications:\n7+ years of sales experience\nExperience with Salesforce\n"
    )
    out = scoring.score({"title": "Solutions Consultant", "description": desc})
    assert out["grade"] == "high" and not out["disqualified"]
    assert any("preferred, not required" in r for r in out["reasons"])


def test_required_years_after_preferred_heading_still_count():
    import scoring
    desc = _BODY + (
        "\nNice to have:\n6+ years of sales experience\n"
        "\nMinimum Qualifications:\n6+ years of sales experience\n"
    )
    out = scoring.score({"title": "Solutions Consultant", "description": desc})
    assert out["disqualified"]


def test_preferred_cs_degree_does_not_disqualify():
    import scoring
    desc = _BODY + "\nPreferred:\nBachelor's degree in Computer Science\n"
    assert not scoring.score({"title": "Solutions Engineer", "description": desc})["disqualified"]


def test_disqualified_job_is_left_off_the_list():
    import daily_digest
    job = {"title": "Sales Engineer", "fit": "low", "disqualified": True, "url": "https://x"}
    assert daily_digest.omit_reason(job) == "not qualified"
    assert daily_digest.listable([job]) == []


def test_fit_bare_engineering_title_high_only_with_entry_evidence():
    plain = _BODY + _STACK + "2 years of experience."
    friendly = _BODY + _STACK + "Bootcamp graduates welcome. Bachelor's degree or equivalent experience."
    assert pipeline.fit("Full Stack Developer", description=plain) == "medium"
    assert pipeline.fit("Full Stack Developer", description=friendly) == "high"


def test_fit_three_engineering_years_is_low_without_entry_wording():
    desc = _BODY + "3+ years of full-stack development experience with React."
    assert pipeline.fit("Full Stack Developer", description=desc) == "low"


def test_fit_preferred_requirements_are_ignored():
    desc = _BODY + _STACK + "1 year of experience. 5+ years of experience is a plus."
    assert pipeline.fit("Junior Software Engineer", description=desc) == "high"


def test_fit_offshore_firm_is_low():
    job = {"title": "Full Stack Developer", "company": "Alternative Path Private Limited", "description": _BODY}
    import scoring
    assert scoring.score(job)["grade"] == "low"


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
    ("Austin, Texas", "remote", "remote"),
    ("Remote, Texas", "remote", "remote"),
    ("Remote, Washington", "remote", "remote"),
    ("Remote, Canada", "remote", "exclude-intl"),
    ("Remote, Germany", "remote", "exclude-intl"),
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
    assert pipeline.location_high_block("Remote - Canada, USA", "remote")
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
            "company": "Acme, Inc.", "title": "Junior SWE",
            "location": "Edina, MN",
        }
    }
    urls, url_keys, ids, ct = pipeline._seen_indexes(seen)
    assert pipeline._already({"url": "https://x.com/jobs/12345678"}, urls, url_keys, ids, ct)
    assert pipeline._already({"url": "https://x.com/jobs/12345678?utm_source=y"}, urls, url_keys, ids, ct)
    assert pipeline._already({"url": "https://www.x.com/jobs/view/12345678"}, urls, url_keys, ids, ct)
    assert not pipeline._already({"url": "https://other.com/12345678"}, urls, url_keys, ids, ct)
    assert not pipeline._already({"url": "https://other.com/jobs/12345678"}, urls, url_keys, ids, ct)
    same_city = {"url": "https://new.com/1", "company": "Acme Inc", "title": "junior swe", "location": "Edina, Minnesota"}
    assert pipeline._already(same_city, urls, url_keys, ids, ct)
    other_city = {"url": "https://new.com/2", "company": "Acme Inc", "title": "Junior SWE", "location": "Austin, TX"}
    assert not pipeline._already(other_city, urls, url_keys, ids, ct)
    no_city = {"url": "https://new.com/3", "company": "ACME", "title": "junior swe"}
    assert not pipeline._already(no_city, urls, url_keys, ids, ct)
    assert not pipeline._already({"url": "https://new.com/4", "company": "Beta", "title": "SWE", "location": "Edina, MN"}, urls, url_keys, ids, ct)


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


# --- strict disqualification ------------------------------------------------

def _dq(title, extra):
    import scoring
    return scoring.score({"title": title, "description": _BODY + "\n" + extra})


@pytest.mark.parametrize("title,extra", [
    ("Solutions Engineer", "Bachelor's degree in Computer Science or a related technical field."),
    ("Junior Software Engineer", "Bachelor's degree in a technical field is required."),
    ("Solutions Engineer", "Master's degree required."),
    ("Junior Software Engineer", "Strong experience with C++ and Rust."),
    ("Solutions Engineer", "Hands-on experience with Kubernetes and Terraform."),
    ("Solutions Engineer", "AWS Certified Solutions Architect is required."),
    ("Solutions Engineer", "3+ years in a technical role such as software engineering."),
    ("Sales Engineer", "This is a senior-level role."),
])
def test_strict_requirements_disqualify(title, extra):
    out = _dq(title, extra)
    assert out["disqualified"] and out["grade"] == "low"


@pytest.mark.parametrize("title,extra", [
    ("Solutions Engineer", "Bachelor's degree in Computer Science, business, or equivalent experience."),
    ("Junior Software Engineer", "Experience with Java or Python."),
    ("Solutions Engineer", "Preferred: AWS Certified Solutions Architect."),
    ("Solutions Engineer", "Nice to have:\nMaster's degree\nKubernetes experience"),
])
def test_alternatives_and_preferred_do_not_disqualify(title, extra):
    assert not _dq(title, extra)["disqualified"]


def test_bare_engineering_title_with_no_entry_signal_is_low():
    out = _dq("Software Engineer", "You will build features with the team.")
    assert out["grade"] == "low"


# --- paged search and the posting window ------------------------------------

def _fake_pages(monkeypatch, pages_by_number, total=None):
    calls = []

    def fake(portal, args_list):
        page = int(args_list[args_list.index("--page") + 1])
        calls.append((portal, list(args_list)))
        rows = pages_by_number.get(page, [])
        meta = {"count": len(rows), "page": page}
        if total is not None:
            meta["total"] = total
        return 0, json.dumps({"meta": meta, "results": rows}), ""

    monkeypatch.setattr(pipeline, "_cli_page", fake)
    return calls


def _rows(start, n):
    return [{"id": str(i), "url": f"https://x/{i}", "title": "t"} for i in range(start, start + n)]


def test_cli_query_reads_every_page_until_short(tmp_path, monkeypatch):
    calls = _fake_pages(monkeypatch, {1: _rows(0, 10), 2: _rows(10, 10), 3: _rows(20, 4)})
    monkeypatch.setitem(pipeline.DEFAULT_SEARCH_SETTINGS, "page_delay_seconds", 0)
    rc = pipeline._run_cli_query("li_t", "linkedin-search", ["search", "--limit", "20", "--jobage", "14"], tmp_path)
    data = json.loads((tmp_path / "li_t.json").read_text(encoding="utf-8"))
    assert rc == 0 and len(data["results"]) == 24 and data["meta"]["pages_read"] == 3
    assert len(calls) == 3


def test_cli_query_overrides_limit_and_jobage(tmp_path, monkeypatch):
    calls = _fake_pages(monkeypatch, {1: _rows(0, 3)})
    pipeline._run_cli_query("fh_t", "freehire-search", ["search", "--limit", "20", "--jobage", "14"], tmp_path, jobage_days=2)
    args = calls[0][1]
    assert args[args.index("--limit") + 1] == "50"
    assert args[args.index("--jobage") + 1] == "2"
    assert args.count("--page") == 1


def test_cli_query_stops_at_page_cap_and_at_total(tmp_path, monkeypatch):
    settings = {"page_size": {"linkedin-search": 10}, "max_pages": {"linkedin-search": 2}, "page_delay_seconds": 0}
    calls = _fake_pages(monkeypatch, {n: _rows(n * 10, 10) for n in range(1, 6)})
    pipeline._run_cli_query("li_t", "linkedin-search", ["search"], tmp_path, settings)
    assert len(calls) == 2
    calls.clear()
    _fake_pages(monkeypatch, {1: _rows(0, 10), 2: _rows(10, 10)}, total=10)
    pipeline._run_cli_query("li_u", "linkedin-search", ["search"], tmp_path, {**settings, "max_pages": {"linkedin-search": 5}})
    assert len(json.loads((tmp_path / "li_u.json").read_text(encoding="utf-8"))["results"]) == 10


def test_cli_query_keeps_pages_read_when_a_later_page_fails(tmp_path, monkeypatch):
    def fake(portal, args_list):
        page = int(args_list[args_list.index("--page") + 1])
        if page == 2:
            return 1, "not json", "rate limited"
        return 0, json.dumps({"meta": {}, "results": _rows(0, 10)}), ""

    monkeypatch.setattr(pipeline, "_cli_page", fake)
    rc = pipeline._run_cli_query("li_t", "linkedin-search", ["search"], tmp_path, {**pipeline.DEFAULT_SEARCH_SETTINGS, "page_delay_seconds": 0})
    assert rc == 0 and len(json.loads((tmp_path / "li_t.json").read_text(encoding="utf-8"))["results"]) == 10


def test_search_window_follows_last_run(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(pipeline, "LAST_RUN_PATH", tmp_path / "last_run.json")
    st = pipeline.DEFAULT_SEARCH_SETTINGS
    now = datetime(2026, 10, 22, 13, 0)
    assert pipeline.search_window_days(st, now) == 14          # no record: widest
    pipeline.LAST_RUN_PATH.write_text(json.dumps({"search_finished": (now - timedelta(hours=5)).isoformat()}))
    assert pipeline.search_window_days(st, now) == 1           # 5 hours ago
    pipeline.LAST_RUN_PATH.write_text(json.dumps({"search_finished": (now - timedelta(days=3, hours=1)).isoformat()}))
    assert pipeline.search_window_days(st, now) == 5           # computer was off
    pipeline.LAST_RUN_PATH.write_text(json.dumps({"search_finished": (now - timedelta(days=40)).isoformat()}))
    assert pipeline.search_window_days(st, now) == 14          # capped


@pytest.mark.parametrize("title", ["Sales Engineer", "Implementation Consultant", "Software Engineer"])
def test_worth_reading_titles_only_the_text_can_decide(title):
    import scoring
    assert scoring.worth_reading({"title": title, "company": "Acme"})


@pytest.mark.parametrize("title", ["Senior Solutions Engineer", "Supplier Quality Engineer", "Software Intern"])
def test_worth_reading_skips_titles_that_are_low_regardless_of_text(title):
    import scoring
    assert not scoring.worth_reading({"title": title, "company": "Acme"})


@pytest.mark.parametrize("title", [
    "CTIO-AI Engineer-Sr Associate",
    "Forward Deployed Software Engineer-Experienced Associate",
    "Clinical Solutions Consultant - PACS",
    "Retail Quality Assurance Coordinator",
])
def test_title_traps_are_not_graded_up(title):
    import scoring
    long_text = "You will work with customers. " * 40
    out = scoring.score({"title": title, "company": "Acme", "description": long_text, "body_status": "opened"})
    assert out["grade"] == "low"


def test_url_variants_collapse_to_one_job(tmp_path):
    base = "https://www.arbeitnow.com/jobs/companies/x/remote-solutions-consultant-1"
    (tmp_path / "a.json").write_text(json.dumps({"results": [
        {"title": "Solutions Consultant", "company": "X", "url": base},
        {"title": "Solutions Consultant", "company": "X", "url": base + "?utm_source=freehire.me"},
    ]}), encoding="utf-8")
    jobs, _ = pipeline._load_run_jobs(tmp_path)
    assert len(jobs) == 1


def test_workday_posting_maps_to_its_json_endpoint():
    import posting_text
    url = "https://blackbaud.wd1.myworkdayjobs.com/ExternalCareers/job/Remote---USA/TAM_R001?utm_source=x"
    assert posting_text._ats_api_url(url) == (
        "https://blackbaud.wd1.myworkdayjobs.com/wday/cxs/blackbaud/ExternalCareers/job/Remote---USA/TAM_R001"
    )
    raw = json.dumps({"jobPostingInfo": {"jobDescription": "<p>Five years of sales.</p><li>Python</li>"}})
    assert "Five years of sales." in posting_text._from_ats_json(raw)


def test_himalayas_source_keeps_text_and_dedupes(monkeypatch):
    job = {"title": "Sales Engineer", "companyName": "Acme", "guid": "https://himalayas.app/companies/acme/jobs/sales-engineer",
           "description": "<p>" + "Demo the product. " * 30 + "</p>", "pubDate": "1792654690"}
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None: {"jobs": [job]})
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    out = pipeline.src_himalayas()
    assert len(out) == 1  # the same job came back for every query
    assert out[0]["portal"] == "himalayas" and out[0]["work_mode"] == "remote"
    assert len(out[0]["description"]) > 300


def test_backfill_files_jobs_under_their_posting_day():
    import daily_digest
    from datetime import date
    store = {
        "a": {"found_on": "2026-10-22", "date": "2026-10-20"},   # posted 2 days ago
        "b": {"found_on": "2026-10-22", "date": "2026-10-22"},   # posted today
        "c": {"found_on": "2026-10-22", "date": "2026-10-01"},   # older than the backfill
        "d": {"found_on": "2026-10-19", "date": "2026-10-19"},   # an earlier run's job
    }
    moved = daily_digest.backfill_found_on(store, date(2026, 10, 22), 3)
    assert moved == {"2026-10-20": 1}
    assert store["a"]["found_on"] == "2026-10-20" and store["a"]["backfilled"]
    assert store["b"]["found_on"] == store["c"]["found_on"] == "2026-10-22"
    assert store["d"]["found_on"] == "2026-10-19"


def test_a_development_perk_is_not_a_required_tool():
    import scoring
    text = "Professional Development: work with Terraform, OpenTelemetry and strong tooling every day."
    assert scoring.missing_skill(text) == ""


def test_same_job_on_two_sites_keeps_the_employers_link():
    jobs = [
        {"title": "Sales Engineer", "company": "Acme, Inc.", "location": "Remote (United States)",
         "url": "https://www.linkedin.com/jobs/view/1", "portal": "linkedin-search"},
        {"title": "Sales Engineer", "company": "Acme Inc", "location": "Remote",
         "url": "https://boards.greenhouse.io/acme/jobs/99", "portal": "freehire-search"},
        {"title": "Sales Engineer", "company": "Acme Inc", "location": "Minneapolis, MN",
         "url": "https://boards.greenhouse.io/acme/jobs/100", "portal": "freehire-search"},
    ]
    kept, dropped = pipeline.collapse_duplicates(jobs)
    assert dropped == 1
    urls = [j["url"] for j in kept]
    assert "https://boards.greenhouse.io/acme/jobs/99" in urls and "https://www.linkedin.com/jobs/view/1" not in urls
    assert "https://boards.greenhouse.io/acme/jobs/100" in urls  # a different city is a different job
    employer = next(j for j in kept if j["url"].endswith("/99"))
    assert employer["also_on"] == ["linkedin-search"]


POSTING = ("Industry Solutions Engineer. You will partner with account executives to run technical discovery, "
           "design solutions for enterprise customers and present them to executives. " * 2)


def test_same_posting_under_two_place_labels_is_one_job_and_keeps_the_better_place():
    chicago = {"title": "Industry Solutions Engineer", "company": "CISCO Systems", "location": "Chicago, IL", "tier": "exclude-east",
               "url": "https://jooble.org/desc/1", "portal": "jooble", "description": POSTING}
    minneapolis = {**chicago, "location": "Minneapolis, MN", "tier": "mn", "url": "https://jooble.org/desc/2"}
    kept, dropped = pipeline.collapse_duplicates([chicago, minneapolis])
    assert dropped == 1 and kept[0]["location"] == "Minneapolis, MN"
    assert kept[0]["also_places"] == ["Chicago, IL"]
    kept, dropped = pipeline.collapse_duplicates([  # order does not matter
        {**minneapolis, "also_places": []}, {**chicago}])
    assert dropped == 1 and kept[0]["location"] == "Minneapolis, MN"
    assert kept[0]["also_places"] == ["Chicago, IL"]


def test_two_place_labels_stay_apart_without_the_same_posting_text():
    a = {"title": "Sales Engineer", "company": "Acme", "location": "Chicago, IL", "tier": "unknown", "url": "https://x.com/1", "description": POSTING}
    b = {**a, "location": "Minneapolis, MN", "tier": "mn", "url": "https://x.com/2", "description": "A different posting. " * 20}
    c = {**a, "location": "Austin, TX", "url": "https://x.com/3", "description": "short"}
    d = {**a, "location": "Denver, CO", "url": "https://x.com/4", "description": "short"}
    kept, dropped = pipeline.collapse_duplicates([a, b, c, d])
    assert dropped == 0 and len(kept) == 4


def test_a_shared_template_at_two_companies_is_not_one_job():
    a = {"title": "Junior Backend Developer", "company": "MENA Alliances", "location": "Remote", "tier": "remote", "url": "https://x.com/1", "description": POSTING}
    b = {**a, "company": "Alliants Limited", "url": "https://x.com/2"}
    kept, dropped = pipeline.collapse_duplicates([a, b])
    assert dropped == 0 and len(kept) == 2


def test_company_key_joins_known_spellings_of_one_employer():
    assert pipeline.company_key("Flextronics International") == pipeline.company_key("flextronics")
    assert pipeline.company_key("hpe") == pipeline.company_key("Hewlett Packard Enterprise")
    assert pipeline.company_key("International Paper") != pipeline.company_key("Paper")


def test_store_skips_a_job_it_already_holds_from_another_site():
    import daily_digest
    store = {}
    first = {"title": "Sales Engineer", "company": "Acme", "location": "Remote", "url": "https://x.com/a/1", "description": "d"}
    twin = {"title": "Sales Engineer", "company": "Acme", "location": "Remote (United States)", "url": "https://y.com/b/2", "description": "d"}
    daily_digest.merge_jobs(store, [first], "2026-10-22")
    daily_digest.merge_jobs(store, [twin], "2026-10-22")
    assert len(store) == 1


def test_jsearch_is_skipped_without_a_key(monkeypatch):
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    with pytest.raises(pipeline.SkipSource):
        pipeline.src_jsearch()


def test_jsearch_maps_a_result(monkeypatch, tmp_path):
    monkeypatch.setenv("RAPIDAPI_KEY", "k")
    monkeypatch.setattr(pipeline, "SCRAPER_DIR", tmp_path)
    row = {"job_title": "Sales Engineer", "employer_name": "Acme", "job_city": "Edina", "job_state": "MN",
           "job_is_remote": False, "job_apply_link": "https://acme.com/j/1", "job_id": "abc",
           "job_posted_at_datetime_utc": "2026-10-21T10:00:00.000Z", "job_description": "Demo things."}
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None, timeout=None: {"data": [row]})
    out = pipeline.src_jsearch()
    assert out[0]["location"] == "Edina, MN" and out[0]["date"] == "2026-10-21" and out[0]["portal"] == "jsearch"


def test_collapse_store_prefers_the_employers_link():
    import daily_digest
    store = {
        "li": {"title": "Sales Engineer", "company": "Acme", "location": "Remote", "url": "https://www.linkedin.com/jobs/view/1"},
        "gh": {"title": "Sales Engineer", "company": "Acme", "location": "Remote", "url": "https://boards.greenhouse.io/acme/jobs/9"},
        "other": {"title": "Solutions Engineer", "company": "Acme", "location": "Remote", "url": "https://boards.greenhouse.io/acme/jobs/10"},
    }
    assert daily_digest.collapse_store(store) == 1
    assert set(store) == {"gh", "other"}


def _jsearch_setup(monkeypatch, tmp_path, cfg):
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    monkeypatch.setattr(pipeline, "SCRAPER_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "TODAY", "2026-10-22")
    (tmp_path / "queries.json").write_text(json.dumps({"jsearch": cfg}))
    monkeypatch.setattr(pipeline, "QUERIES_PATH", tmp_path / "queries.json")
    seen = []
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None, timeout=None: seen.append(url) or {"data": []})
    return seen


def test_jsearch_stops_at_the_monthly_limit(monkeypatch, tmp_path):
    _jsearch_setup(monkeypatch, tmp_path, {"queries": ["a"], "monthly_limit": 10})
    (tmp_path / "jsearch_runs.json").write_text(json.dumps({"month": "2026-10", "requests": 10}))
    with pytest.raises(pipeline.SkipSource, match="monthly"):
        pipeline.src_jsearch()
    (tmp_path / "jsearch_runs.json").write_text(json.dumps({"month": "2026-09", "requests": 10}))
    assert pipeline.src_jsearch() == []  # a new month starts a new budget


def test_jsearch_sends_every_query_each_run(monkeypatch, tmp_path):
    seen = _jsearch_setup(monkeypatch, tmp_path, {"queries": ["a", "b", "c"], "monthly_limit": 100})
    pipeline.src_jsearch()
    pipeline.src_jsearch()
    assert len(seen) == 6
    assert json.loads((tmp_path / "jsearch_runs.json").read_text())["requests"] == 6


def test_the_scheduler_owns_the_cadence():
    assert not hasattr(pipeline, "source_due") and not hasattr(pipeline, "_rotate")


def test_workable_keeps_recent_us_jobs(monkeypatch):
    from datetime import date
    today = date.today().isoformat()
    jobs = [
        {"title": "Solutions Engineer", "url": "https://jobs.workable.com/view/a", "id": "a", "created": today,
         "workplace": "remote", "location": {"countryName": "United States"}, "company": {"title": "Acme"},
         "description": "<p>demo</p>", "requirementsSection": ""},
        {"title": "Solutions Engineer", "url": "https://jobs.workable.com/view/b", "id": "b", "created": today,
         "location": {"countryName": "Canada"}, "company": {"title": "Maple"}},
        {"title": "Solutions Engineer", "url": "https://jobs.workable.com/view/c", "id": "c", "created": "2020-01-01",
         "location": {"countryName": "United States"}, "company": {"title": "Old"}},
    ]
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None, timeout=20: {"jobs": jobs})
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    out = pipeline.src_workable()
    assert [j["company"] for j in out] == ["Acme"] and out[0]["work_mode"] == "remote"


def test_simplify_filters_inactive_and_old(monkeypatch):
    import json, time as _t
    now = int(_t.time())
    rows = [
        {"title": "Software Engineer", "company_name": "A", "url": "https://a.example/1", "locations": ["Remote"],
         "active": True, "is_visible": True, "date_posted": now},
        {"title": "Software Engineer", "company_name": "B", "url": "https://b.example/1", "locations": ["NYC"],
         "active": False, "is_visible": True, "date_posted": now},
        {"title": "Software Engineer", "company_name": "C", "url": "https://c.example/1", "locations": ["NYC"],
         "active": True, "is_visible": True, "date_posted": now - 90 * 86400},
    ]
    monkeypatch.setattr(pipeline, "_get", lambda url, headers=None, timeout=20: json.dumps(rows))
    out = pipeline.src_simplify()
    assert [j["company"] for j in out] == ["A"] and out[0]["work_mode"] == "remote"


@pytest.mark.parametrize("source", ["remoteok", "weworkremotely", "workable", "muse"])
def test_multi_request_source_fails_when_every_request_fails(monkeypatch, source):
    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(pipeline, "_get_json", boom)
    monkeypatch.setattr(pipeline, "_get", boom)
    with pytest.raises(RuntimeError, match="request"):
        getattr(pipeline, "src_" + source)()


def _http_error(code, retry_after=None):
    import email.message
    import urllib.error
    hdrs = email.message.Message()
    if retry_after:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", code, "err", hdrs, None)


def test_get_waits_out_a_429_and_retries(monkeypatch):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"ok"

    calls, waits = [], []
    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(429, "7")
        return Resp()
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)
    assert pipeline._get("http://x") == "ok" and waits == [7, 7]


def test_get_gives_up_after_three_429s_and_never_retries_a_404(monkeypatch):
    import urllib.error
    calls = []
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", lambda req, timeout=None: calls.append(1) or (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(urllib.error.HTTPError):
        pipeline._get("http://x")
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", lambda req, timeout=None: calls.append(1) or (_ for _ in ()).throw(_http_error(404)))
    with pytest.raises(urllib.error.HTTPError):
        pipeline._get("http://x")
    assert len(calls) == 1


def test_remotive_runs_four_times_a_day(monkeypatch):
    import datetime as real
    class FakeDT(real.datetime):
        hour_now = 6
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 27, cls.hour_now)
    monkeypatch.setattr(pipeline, "datetime", FakeDT)
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None, timeout=None: {"jobs": []})
    ran = []
    for h in range(6, 19):
        FakeDT.hour_now = h
        try:
            pipeline.src_remotive(); ran.append(h)
        except pipeline.SkipSource:
            pass
    assert ran == [6, 10, 14, 18]


def test_jooble_needs_a_key(monkeypatch):
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    with pytest.raises(pipeline.SkipSource):
        pipeline.src_jooble()


def test_jooble_maps_and_dedupes_results(monkeypatch, tmp_path):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    (tmp_path / "queries.json").write_text(json.dumps({"jooble": {"queries": ["a", "b"], "locations": ["Minnesota", "remote"]}}))
    monkeypatch.setattr(pipeline, "QUERIES_PATH", tmp_path / "queries.json")
    row = {"title": "Solutions Engineer", "company": "Acme", "location": "Minneapolis, MN", "link": "https://jooble.org/desc/1",
           "id": 1, "updated": "2026-10-24T00:00:00.0000000", "snippet": "Demo <b>things</b>."}
    sent = []
    def fake(url, headers=None, timeout=None, data=None):
        sent.append(json.loads(data)); return {"jobs": [row]}
    monkeypatch.setattr(pipeline, "_get_json", fake)
    out = pipeline.src_jooble()
    assert len(sent) == 4 and len(out) == 1
    assert out[0]["portal"] == "jooble" and out[0]["date"] == "2026-10-24" and "<b>" not in out[0]["description"]


def test_a_post_body_makes_get_a_post(monkeypatch):
    seen = {}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    def fake(req, timeout=None):
        seen["method"] = req.get_method(); return Resp()
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake)
    pipeline._get("http://x", data=b"{}")
    assert seen["method"] == "POST"


def test_state_limited_remote_job_is_a_relocation_medium():
    block = pipeline.location_high_block("Remote (United States)", "remote", "Candidates in TX/OK/AR/LA only.")
    assert block and "relocate" in block
    assert pipeline.location_high_block("Remote (United States)", "remote", "Candidates in MN/WI only.") is None


def test_company_key_ignores_legal_suffixes_and_board_labels():
    assert pipeline.company_key("Rithum") == pipeline.company_key("Rithum LinkedIn Board")
    assert pipeline.company_key("Billtrust US Careers") == pipeline.company_key("Billtrust US")
    assert pipeline.company_key("SHI International Corp.") == pipeline.company_key("SHI International")
    assert pipeline.company_key("Epic") != pipeline.company_key("Epic Systems")


def test_same_job_under_two_company_spellings_is_one_job():
    a = {"company": "Rithum", "title": "Technical Account Manager", "location": "United States - Remote"}
    b = {"company": "Rithum LinkedIn Board", "title": "Technical Account Manager", "location": "United States - Remote"}
    assert pipeline.dedupe_key(a) == pipeline.dedupe_key(b)


def test_collapse_store_drops_a_repost_whose_employer_page_is_stored():
    import daily_digest

    store = {
        "himalayas": {"url": "https://himalayas.app/companies/acme/jobs/se", "company": "Acme", "title": "SE",
                      "apply_url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        "greenhouse": {"url": "https://job-boards.greenhouse.io/acme/jobs/1", "company": "Acme Inc", "title": "Other"},
    }
    assert daily_digest.collapse_store(store) == 1
    assert list(store) == ["greenhouse"]


def _posting_text():
    import posting_text

    return posting_text


def test_board_slugs_come_from_the_company_and_the_himalayas_page():
    pt = _posting_text()
    job = {"company": "Net at Work, LLC", "url": "https://himalayas.app/companies/net-at-work-llc/jobs/tam"}
    slugs = pt.board_slugs(job)
    assert slugs[0] == "net-at-work-llc" and "netatwork" in slugs and "net-at-work" in slugs


def test_resolve_apply_needs_the_exact_title(monkeypatch):
    pt = _posting_text()
    rows = [
        {"title": "Solutions Engineer", "url": "https://jobs.example/a", "text": "full text", "location": "Remote - US"},
        {"title": "Senior Solutions Engineer", "url": "https://jobs.example/b", "text": "x", "location": "Remote - US"},
    ]
    monkeypatch.setattr(pt, "_board_jobs", lambda ats, slug: rows if (ats, slug) == ("ashby", "acme") else [])
    job = {"title": "Solutions Engineer", "company": "Acme", "location": "Remote (United States)"}
    assert pt.resolve_apply(job) == ("https://jobs.example/a", "full text", "ashby")
    assert pt.resolve_apply(dict(job, title="Sales Engineer")) == ("", "", "")


def test_resolve_apply_links_sets_the_employer_link_and_text(monkeypatch):
    pt = _posting_text()
    monkeypatch.setattr(pt, "resolve_apply", lambda job: ("https://jobs.example/a", "Real posting text. " * 40, "lever"))
    aggregator = {"title": "T", "company": "C", "url": "https://himalayas.app/companies/c/jobs/t", "body_status": "unread"}
    own = {"title": "T2", "company": "C", "url": "https://jobs.lever.co/c/1"}
    out = pt.resolve_apply_links([aggregator, own], workers=1)
    assert out == {"found": 1, "tried": 1}
    assert aggregator["apply_url"] == "https://jobs.example/a" and aggregator["body_status"] == "opened"
    assert "apply_url" not in own
    again = pt.resolve_apply_links([aggregator], workers=1)
    assert again["tried"] == 0


def _himalayas_page(*slugs, body="Real posting text. " * 30):
    import json

    return json.dumps({"jobs": [
        {"guid": f"https://himalayas.app/companies/acme/jobs/{s}", "title": s, "description": f"<p>{body}</p>"}
        for s in slugs
    ]})


def test_himalayas_slug_reads_company_job_urls_only():
    pt = _posting_text()
    assert pt._himalayas_slug("https://himalayas.app/companies/acme/jobs/solutions-engineer?utm_source=x") == "solutions-engineer"
    assert pt._himalayas_slug("https://himalayas.app/jobs/api/search?q=x") == ""
    assert pt._himalayas_slug("https://jobs.lever.co/acme/1") == ""


def test_fetch_himalayas_matches_the_slug_in_the_search_results(monkeypatch):
    pt = _posting_text()
    calls = []

    def fake_get(url, accept=""):
        calls.append(url)
        return _himalayas_page("other-role", "solutions-engineer")

    monkeypatch.setattr(pt, "_get", fake_get)
    job = {"url": "https://himalayas.app/companies/acme/jobs/solutions-engineer?utm_source=x", "title": "Solutions Engineer", "company": "Acme"}
    status, text = pt.fetch_himalayas(job)
    assert status == "opened" and text.startswith("Real posting text.")
    assert len(calls) == 1 and "q=Solutions%20Engineer" in calls[0]


def test_fetch_himalayas_tries_title_and_company_then_gives_up_unread(monkeypatch):
    pt = _posting_text()
    calls = []
    monkeypatch.setattr(pt, "_get", lambda url, accept="": calls.append(url) or _himalayas_page("someone-else"))
    job = {"url": "https://himalayas.app/companies/acme/jobs/missing-role", "title": "Missing Role", "company": "Acme"}
    assert pt.fetch_himalayas(job) == ("unread", "")
    assert len(calls) == 2 and "Acme" in calls[1]


def test_fetch_himalayas_survives_a_failing_api(monkeypatch):
    pt = _posting_text()

    def boom(url, accept=""):
        raise OSError("down")

    monkeypatch.setattr(pt, "_get", boom)
    job = {"url": "https://himalayas.app/companies/acme/jobs/x", "title": "X", "company": "Acme"}
    assert pt.fetch_himalayas(job) == ("unread", "")


def test_enrich_reads_himalayas_rows_through_the_api(monkeypatch):
    pt = _posting_text()
    monkeypatch.setattr(pt, "fetch_himalayas", lambda job: ("opened", "Full text from the API. " * 20))
    monkeypatch.setattr(pt, "fetch_text", lambda url, company="": ("unread", ""))
    himalayas = {"url": "https://himalayas.app/companies/acme/jobs/se", "title": "SE", "company": "Acme"}
    other = {"url": "https://example.com/job/1", "title": "T", "company": "C"}
    pt.enrich([himalayas, other], workers=1)
    assert himalayas["body_status"] == "opened" and himalayas["description"].startswith("Full text from the API.")
    assert other["body_status"] == "unread"



def test_title_tied_to_a_relocation_state_caps_high_to_medium():
    block = pipeline.location_high_block("Remote (United States)", "remote", "", "Solutions Engineer, Florida (Enterprise)")
    assert block and "FL" in block
    assert pipeline.location_high_block("Remote (United States)", "remote", "", "Solutions Engineer, Minnesota") is None
    assert pipeline.location_high_block("Remote (United States)", "remote", "", "Solutions Engineer") is None


def test_enrich_fetches_text_that_looks_cut_at_two_thousand_characters(monkeypatch):
    import posting_text as pt

    monkeypatch.setattr(pt, "fetch_text", lambda url, company="": ("opened", "Full posting. " * 400))
    snippet = {"url": "https://boards.greenhouse.io/acme/jobs/1", "company": "Acme", "description": "x" * 2000}
    whole = {"url": "https://boards.greenhouse.io/acme/jobs/2", "company": "Acme", "description": "x" * 1700}
    pt.enrich([snippet, whole], workers=1)
    assert snippet["body_status"] == "opened" and "whole" not in snippet and len(snippet["description"]) > 2000
    assert "body_status" not in whole


def test_embedded_greenhouse_link_is_read_through_the_board_api(monkeypatch):
    import posting_text as pt

    seen = []
    monkeypatch.setattr(pt, "_get", lambda url, accept=None, *a, **k: (seen.append(url), '{"content": "' + "Requirements text. " * 30 + '"}')[1])
    status, text = pt.fetch_text("https://cribl.io/job-detail/?gh_jid=6199340004", "Cribl")
    assert status == "opened" and text.startswith("Requirements text")
    assert seen[0] == "https://boards-api.greenhouse.io/v1/boards/cribl/jobs/6199340004"


def test_failed_refetch_of_a_long_snippet_keeps_it_readable(monkeypatch):
    import posting_text as pt

    monkeypatch.setattr(pt, "fetch_text", lambda url, company="": ("unread", ""))
    job = {"url": "https://example.com/j", "company": "Acme", "description": "x" * 2000}
    pt.enrich([job], workers=1)
    assert "body_status" not in job and len(job["description"]) == 2000
def test_generic_us_remote_keeps_state_names_and_drops_canada():
    assert pipeline._generic_us_remote("Remote (United States)")
    assert pipeline._generic_us_remote("Remote")
    assert not pipeline._generic_us_remote("Remote, Canada")
    assert not pipeline._generic_us_remote("Austin, Texas")
    assert pipeline._card_in_requested_location("Remote (United States)", "remote")
    assert not pipeline._card_in_requested_location("Remote, Canada", "remote")
    assert pipeline.loc_tier("Austin, Texas", "remote") == "remote"


def test_required_language_negation_and_clearance_wording():
    import scoring
    java = scoring.score({"title": "Sales Engineer", "description": _BODY + "Rust is required."})
    assert java["disqualified"]
    skills = scoring.score({"title": "Sales Engineer", "description": _BODY + "Required skills: Scala and Spring."})
    assert skills["disqualified"]
    no_java = scoring.score({"title": "Sales Engineer", "description": _BODY + "No Rust experience required."})
    assert no_java["grade"] == "high" and not no_java["disqualified"]
    no_clear = scoring.score({"title": "Sales Engineer", "description": _BODY + "We do not require a security clearance."})
    assert no_clear["grade"] == "high" and not no_clear["disqualified"]
    active = scoring.score({"title": "Sales Engineer", "description": _BODY + "Must hold an active security clearance."})
    assert active["disqualified"]
    willing = scoring.score({"title": "Sales Engineer", "description": _BODY + "Willing to obtain a security clearance."})
    assert willing["grade"] == "high" and not willing["disqualified"]
    citizen = scoring.score({"title": "Sales Engineer", "description": _BODY + "U.S. citizenship is required."})
    assert citizen["grade"] == "high" and not citizen["disqualified"]
    business = scoring.score({
        "title": "Sales Engineer",
        "description": _BODY + "Bachelor's degree in Computer Science required to support our business.",
    })
    assert business["disqualified"]
    listed = scoring.score({
        "title": "Sales Engineer",
        "description": _BODY + "Bachelor's degree in Computer Science, business, or equivalent.",
    })
    assert listed["grade"] == "high" and not listed["disqualified"]


def test_sign_on_bonus_heading_does_not_hide_a_requirement():
    import scoring
    desc = _BODY + "\nSign-on Bonus:\n5+ years of Java experience required.\n"
    out = scoring.score({"title": "Sales Engineer", "description": desc})
    assert out["disqualified"]


def test_linkedin_jobage_snaps_up_to_a_valid_window(tmp_path, monkeypatch):
    calls = _fake_pages(monkeypatch, {1: _rows(0, 3)})
    pipeline._run_cli_query(
        "li_t", "linkedin-search", ["search", "--jobage", "14"], tmp_path, jobage_days=5,
    )
    args = calls[0][1]
    assert args[args.index("--jobage") + 1] == "7"
    calls.clear()
    pipeline._run_cli_query(
        "li_u", "linkedin-search", ["search"], tmp_path, jobage_days=15,
    )
    args = calls[0][1]
    assert args[args.index("--jobage") + 1] == "30"


def test_search_advances_last_run_when_a_later_page_fails(tmp_path, monkeypatch):
    def fake(portal, args_list):
        page = int(args_list[args_list.index("--page") + 1])
        if page == 2:
            return 1, "not json", "rate limited"
        return 0, json.dumps({"meta": {}, "results": _rows(0, 10)}), ""

    monkeypatch.setattr(pipeline, "_cli_page", fake)
    monkeypatch.setattr(pipeline, "LAST_RUN_PATH", tmp_path / "last_run.json")
    monkeypatch.setattr(pipeline, "QUERIES_PATH", tmp_path / "queries.json")
    (tmp_path / "queries.json").write_text(json.dumps({
        "cli_queries": [{"name": "li_t", "portal": "linkedin-search", "args": ["search", "--jobage", "14"]}],
        "http_sources": ["__none__"],
        "search_settings": {
            "min_days": 1,
            "max_days": 14,
            "page_size": {"linkedin-search": 10},
            "max_pages": {"linkedin-search": 2},
            "page_delay_seconds": 0,
        },
    }), encoding="utf-8")
    out = tmp_path / "run"
    monkeypatch.setattr(pipeline, "run_dir", lambda args: out)
    args = type("A", (), {"only": None, "skip": None, "workers": 1, "window_days": None})()
    assert pipeline.cmd_search(args) == 0
    saved = json.loads((out / "li_t.json").read_text(encoding="utf-8"))
    assert len(saved["results"]) == 10
    assert (tmp_path / "last_run.json").exists()


def test_source_keeps_a_description_past_the_enrich_threshold(monkeypatch):
    body = "A" * 3000
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None, timeout=None: [
        {"legal": True},
        {
            "position": "Sales Engineer",
            "company": "Acme",
            "id": "1",
            "url": "https://remoteok.com/1",
            "description": body,
            "date": "2026-10-01",
            "tags": [],
        },
    ])
    out = pipeline.src_remoteok()
    assert len(out[0]["description"]) == 3000


def test_collapse_keeps_the_longer_description_and_earliest_found_on():
    short = {
        "title": "Sales Engineer", "company": "Acme", "location": "Remote",
        "url": "https://www.linkedin.com/jobs/view/1", "portal": "linkedin-search",
        "description": "short", "found_on": "2026-10-01",
    }
    long = {
        "title": "Sales Engineer", "company": "Acme", "location": "Remote",
        "url": "https://boards.greenhouse.io/acme/jobs/9", "portal": "greenhouse",
        "description": "x" * 2000, "found_on": "2026-10-03",
    }
    kept, dropped = pipeline.collapse_duplicates([short, long])
    assert dropped == 1
    assert kept[0]["url"].endswith("/9")
    assert len(kept[0]["description"]) == 2000
    assert kept[0]["found_on"] == "2026-10-01"
    employer_short = {**long, "description": "short", "found_on": "2026-10-04"}
    aggregator_long = {**short, "description": "y" * 1800, "found_on": "2026-10-02"}
    kept, dropped = pipeline.collapse_duplicates([employer_short, aggregator_long])
    assert dropped == 1
    assert kept[0]["url"].endswith("/9")
    assert len(kept[0]["description"]) == 1800
    assert kept[0]["found_on"] == "2026-10-02"


def test_himalayas_missing_pubdate_is_blank(monkeypatch):
    job = {
        "title": "Sales Engineer", "companyName": "Acme",
        "guid": "https://himalayas.app/companies/acme/jobs/sales-engineer",
        "description": "<p>Demo the product.</p>",
    }
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None: {"jobs": [job]})
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    out = pipeline.src_himalayas()
    assert out[0]["date"] == ""


def test_arbeitnow_remote_outside_the_us_is_not_us_remote(monkeypatch):
    payload = {"data": [
        {"remote": True, "title": "Sales Engineer", "company_name": "Berlin Co", "location": "Berlin",
         "url": "https://arbeitnow.com/1", "slug": "1", "description": "x", "created_at": 1790000000, "tags": []},
        {"remote": True, "title": "Sales Engineer", "company_name": "US Co", "location": "United States",
         "url": "https://arbeitnow.com/2", "slug": "2", "description": "x", "created_at": 1790000000, "tags": []},
        {"remote": True, "title": "Solutions Engineer", "company_name": "Nowhere", "location": "",
         "url": "https://arbeitnow.com/3", "slug": "3", "description": "x", "created_at": 1790000000, "tags": []},
    ], "links": {}}
    monkeypatch.setattr(pipeline, "_get_json", lambda url, headers=None: payload)
    out = {j["url"]: j for j in pipeline.src_arbeitnow()}
    assert pipeline.loc_tier(out["https://arbeitnow.com/1"]["location"], out["https://arbeitnow.com/1"]["work_mode"]) == "exclude-intl"
    assert pipeline.loc_tier(out["https://arbeitnow.com/2"]["location"], out["https://arbeitnow.com/2"]["work_mode"]) == "remote"
    assert pipeline.loc_tier(out["https://arbeitnow.com/3"]["location"], out["https://arbeitnow.com/3"]["work_mode"]) == "unknown"


def test_jsearch_does_not_burn_a_failed_day(monkeypatch, tmp_path):
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    monkeypatch.setattr(pipeline, "SCRAPER_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "QUERIES_PATH", tmp_path / "queries.json")
    (tmp_path / "queries.json").write_text(json.dumps({
        "jsearch": {
            "queries": ["solutions engineer remote", "junior software engineer"],
            "pages": 2,
            "runs_per_day": 1,
            "monthly_limit": 190,
        }
    }), encoding="utf-8")

    def boom(url, headers=None, timeout=None):
        raise OSError("down")

    monkeypatch.setattr(pipeline, "_get_json", boom)
    assert pipeline.src_jsearch() == []
    assert not (tmp_path / "jsearch_runs.json").exists()
    calls = []

    def ok(url, headers=None, timeout=None):
        calls.append(url)
        return {"data": []}

    monkeypatch.setattr(pipeline, "_get_json", ok)
    assert pipeline.src_jsearch() == []
    assert "remote_jobs_only=true" in calls[0]
    assert "in%20United%20States" not in calls[0]
    assert "in%20United%20States" in calls[1]
    assert "remote_jobs_only" not in calls[1]
    budget = json.loads((tmp_path / "jsearch_runs.json").read_text(encoding="utf-8"))
    assert budget["requests"] == 4
    assert budget["count"] == 1


def test_jsearch_corrupt_budget_is_not_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    monkeypatch.setattr(pipeline, "SCRAPER_DIR", tmp_path)
    path = tmp_path / "jsearch_runs.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(pipeline.SkipSource, match="unreadable"):
        pipeline.src_jsearch()
    assert path.read_text(encoding="utf-8") == "{not json"
    path.write_text(json.dumps(["nope"]), encoding="utf-8")
    with pytest.raises(pipeline.SkipSource, match="unreadable"):
        pipeline.src_jsearch()
    assert json.loads(path.read_text(encoding="utf-8")) == ["nope"]


def test_jsonld_type_list_and_ats_error_fallback(monkeypatch):
    import urllib.error
    import posting_text

    html = (
        '<script type="application/ld+json">'
        '{"@type": ["JobPosting"], "description": "<p>Five years of Python.</p>"}'
        "</script>"
    )
    assert "Five years of Python." in posting_text._jsonld_description(html)
    page = "<html>" + html.replace("Five years of Python.", "Sell the product. " * 20) + "</html>"

    def fake_get(url, accept="text/html,application/json"):
        if "boards-api.greenhouse.io" in url:
            raise urllib.error.HTTPError(url, 403, "no", hdrs=None, fp=None)
        return page

    monkeypatch.setattr(posting_text, "_get", fake_get)
    monkeypatch.setattr(posting_text.time, "sleep", lambda _s: None)
    status, text = posting_text.fetch_text("https://boards.greenhouse.io/acme/jobs/123456")
    assert status == "opened"
    assert "Sell the product." in text


def test_enrich_refetches_a_long_shell(monkeypatch):
    import posting_text

    calls = []

    def fake(url, company=""):
        calls.append(url)
        return "opened", "Real posting. " * 40

    monkeypatch.setattr(posting_text, "fetch_text", fake)
    shell = {"url": "https://example.com/s", "description": "x" * 2000, "body_status": "shell"}
    opened = {"url": "https://example.com/o", "description": "y" * 2000, "body_status": "opened"}
    posting_text.enrich([shell, opened])
    assert calls == ["https://example.com/s"]
    assert shell["body_status"] == "opened"
