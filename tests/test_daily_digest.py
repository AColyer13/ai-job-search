"""Prune rules for daily/latest.txt. No network."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import daily_digest  # noqa: E402


def test_prune_drops_jobs_found_14_days_ago():
    today = date(2026, 10, 19)
    store = {
        "old": {"found_on": "2026-10-05", "title": "Old"},
        "edge": {"found_on": "2026-10-05", "title": "Fourteen days"},
        "keep": {"found_on": "2026-10-06", "title": "Thirteen days"},
        "new": {"found_on": "2026-10-19", "title": "Today"},
    }
    # 2026-10-05 is 14 days before 2026-10-19.
    kept, removed = daily_digest.prune_jobs(store, today, window_days=14)
    assert removed == 2
    assert set(kept) == {"keep", "new"}


def test_date_file_does_not_include_another_day():
    store = {
        "today": {"found_on": "2026-10-19", "title": "Today", "url": "https://example.com/a"},
        "yesterday": {"found_on": "2026-10-18", "title": "Yesterday", "url": "https://example.com/b"},
    }
    titles = [job["title"] for job in daily_digest.jobs_found_on(store, "2026-10-19")]
    assert titles == ["Today"]


def test_type_order_puts_resume_match_ahead_of_senior_sections():
    keys = [key for key, _label in daily_digest.TYPE_ORDER]
    assert keys == [
        "technical_sales",
        "sales_engineer",
        "engineering",
        "customer_engineer",
        "presales",
        "qa",
        "solutions_architect",
        "other",
    ]


def test_position_types_split_consultant_and_developer():
    assert daily_digest.position_type("Solutions Consultant") == "presales"
    assert daily_digest.position_type("Software Developer") == "engineering"
    jobs = [
        {"title": "Solutions Consultant", "fit": "high", "url": "https://example.com/a"},
        {"title": "Software Developer", "fit": "medium", "url": "https://example.com/b"},
    ]
    sections = {section["key"]: [job["title"] for job in section["jobs"]] for section in daily_digest.type_sections(jobs)}
    assert sections["presales"] == ["Solutions Consultant"]
    assert sections["engineering"] == ["Software Developer"]
    assert sum(len(titles) for titles in sections.values()) == 2


def test_position_type_families():
    assert daily_digest.position_type("Technical Sales Engineer - North Central US") == "technical_sales"
    assert daily_digest.position_type("Sales Engineer") == "sales_engineer"
    assert daily_digest.position_type("Entry-Level Technical Solutions Engineer") == "sales_engineer"
    assert daily_digest.position_type("Presales Solutions Engineer") == "sales_engineer"
    assert daily_digest.position_type("Pre-Sales Engineer") == "presales"
    assert daily_digest.position_type("Pre-Sales Engineer- Geospatial Solutions") == "presales"
    assert daily_digest.position_type("Software Engineers (Senior, Staff, Principal)") == "engineering"
    assert daily_digest.position_type("Field Application Engineer - Central US") == "sales_engineer"
    assert daily_digest.position_type("IFS Solution Architect") == "solutions_architect"
    assert daily_digest.position_type("Pre Sales Digital Architect") == "solutions_architect"
    assert daily_digest.position_type("Presales -HCM Solution Consultant - Healthcare - East") == "presales"
    assert daily_digest.position_type("Implementation Consultant") == "customer_engineer"
    assert daily_digest.position_type("Implementation Manager") == "customer_engineer"
    assert daily_digest.position_type("Onboarding Engineer") == "customer_engineer"
    assert daily_digest.position_type("Junior QA Tester") == "qa"
    assert daily_digest.position_type("3rd Shift, Quality Assurance Technician") == "other"
    assert daily_digest.position_type(".NET Developer (Junior)") == "engineering"
    assert daily_digest.position_type("Customer Success Engineer") == "other"


def test_docx_groups_titles_under_different_headings(tmp_path):
    import zipfile

    path = tmp_path / "sample.docx"
    jobs = [
        {"title": "Solutions Consultant", "fit": "high", "company": "A", "url": "https://example.com/a", "found_on": "2026-10-19"},
        {"title": "Junior Software Developer", "fit": "high", "company": "B", "url": "https://example.com/b", "found_on": "2026-10-19"},
    ]
    daily_digest.write_docx(path, "Test", ["Intro"], jobs)
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    assert xml.count("Software Developer") == 1
    assert xml.count("Solutions Consultant") == 1
    assert xml.index('w:name="type_engineering"') < xml.index("Software Developer")
    assert xml.index('w:name="type_presales"') < xml.index("Solutions Consultant")
    assert "Heading1" in xml


def test_engineering_level_ii_and_unlabeled_titles_are_not_listed():
    assert daily_digest.omit_reason({"title": "Software Engineer II, Backend"}) == "engineering level"
    assert daily_digest.omit_reason({"title": "Software Engineer 2 - CorePointAI"}) == "engineering level"
    assert daily_digest.omit_reason({"title": "Systems Development Engineer II"}) == "engineering level"
    assert daily_digest.omit_reason({"title": "Senior Software Engineer"}) == "engineering level"
    assert daily_digest.omit_reason({"title": "Full Stack Developer"}) == "engineering level"
    assert daily_digest.omit_reason({"title": ".NET Developer (Junior)"}) is None
    assert daily_digest.omit_reason({"title": "Software Engineer (Early Career)"}) is None
    assert daily_digest.omit_reason({"title": "Systems Development Engineer I"}) is None
    assert daily_digest.omit_reason({"title": "Technical Sales Trainee (Minneapolis)"}) is None
    assert daily_digest.omit_reason({"title": "Sales Engineer"}) is None


def test_exclusive_engineering_degree_requirement_is_not_listed():
    required = "Qualifications. Bachelor's degree in Computer Science or Engineering required."
    assert daily_digest.omit_reason({"title": "Sales Engineer", "description": required}) == "degree"
    balluff = "Bachelor's degree in engineering, business, or a technical discipline."
    assert daily_digest.exclusive_engineering_degree(balluff) == ""
    equivalent = "BS in Computer Science or equivalent practical experience."
    assert daily_digest.exclusive_engineering_degree(equivalent) == ""
    preferred = "Bachelor's degree in Computer Science preferred."
    assert daily_digest.exclusive_engineering_degree(preferred) == ""
    any_degree = "Bachelor's degree required."
    assert daily_digest.exclusive_engineering_degree(any_degree) == ""
    related = "Bachelor's degree in Computer Science or related field."
    assert daily_digest.exclusive_engineering_degree(related)
    prose = "A computer science degree is required to support our business."
    assert daily_digest.exclusive_engineering_degree(prose)
    assert daily_digest.omit_reason({"title": "Junior Software Developer", "omit": "degree"}) == "degree"


def test_star_marks_resume_education_named_in_the_posting():
    balluff = "Bachelor's degree in engineering, business, or a technical discipline."
    hits = daily_digest.resume_hits(balluff)
    assert [hit["label"] for hit in hits] == ["business or marketing degree"]
    bootcamp = "A coding bootcamp or a computer science degree is welcome."
    assert daily_digest.resume_hits(bootcamp)[0]["label"] == "bootcamp or coding certificate"
    marketing = "Bachelor's degree in marketing or a related field."
    assert daily_digest.resume_hits(marketing)[0]["label"] == "business or marketing degree"
    assert daily_digest.resume_hits("Five years of business development experience.") == []
    assert daily_digest.resume_hits("Bachelor's degree in Computer Science required.") == []
    assert daily_digest.resume_hits("MBA required.") == []


def test_grade_blurb_says_why_and_lows_sit_at_the_bottom(tmp_path):
    import zipfile

    trainee = {
        "title": "Technical Sales Trainee (Minneapolis)",
        "fit": "high",
        "company": "Balluff",
        "location": "Minneapolis, MN",
        "url": "https://example.com/trainee",
        "found_on": "2026-10-19",
        "body_status": "opened",
        "description": "Bachelor's degree in engineering, business, or a technical discipline.",
    }
    architect = {
        "title": "Solutions Architect",
        "fit": "medium",
        "company": "Verge",
        "url": "https://example.com/architect",
        "found_on": "2026-10-19",
        "body_status": "unread",
    }
    other = {
        "title": "Customer Success Engineer",
        "fit": "low",
        "company": "Zero",
        "url": "https://example.com/low",
        "found_on": "2026-10-19",
        "body_status": "opened",
        "description": "Talk with customers after the sale.",
    }
    assert daily_digest._why(trainee).startswith("High. The title is technical sales")
    assert "Posting: Bachelor's degree" in daily_digest._why(trainee)
    assert daily_digest._why(architect).startswith("Medium. The title is a bridge role")
    assert "did not include the job text" in daily_digest._why(architect)
    assert daily_digest._why(other).startswith("Low. The title does not match")
    demoted = {
        "title": "Technical Sales Trainee",
        "fit": "medium",
        "description": "Must have 6 years of sales experience.",
        "body_status": "opened",
    }
    text = daily_digest._why(demoted)
    assert text.startswith("Medium.")
    assert "6 years" in text
    fragment = {
        "title": "Pre-Sales Engineer",
        "fit": "medium",
        "description": "on, and cross-functional collaboration. 8+ years of progressive experience in presales engineering.",
        "body_status": "opened",
    }
    fragment_why = daily_digest._why(fragment)
    assert "Posting: 8+ years of progressive experience in presales engineering." in fragment_why
    assert "on, and cross-functional" not in fragment_why
    nice = {
        "title": "AI Native Account Executive, Compute",
        "fit": "low",
        "description": "alesforce. Qualifications/Nice to Have: 5+ years of full-cycle B2B sales experience.",
        "body_status": "opened",
    }
    nice_why = daily_digest._why(nice)
    assert "Nice to Have: 5+ years of full-cycle B2B sales experience." in nice_why
    chrome = {
        "title": "Solutions Consultant",
        "fit": "high",
        "description": "Careers at Billtrust: See our open jobs and apply. Skip to content.",
        "body_status": "opened",
    }
    assert "did not include the job text" in daily_digest._why(chrome)
    clearance = {
        "title": "Solutions Architect",
        "fit": "low",
        "description": (
            "Work Authorization: U.S. citizenship required. "
            "Clearance: Candidates must be eligible and willing to obtain and maintain a U.S. security clearance, which includes a federal background investigation."
        ),
        "body_status": "opened",
    }
    assert "Candidates must be eligible" in daily_digest._why(clearance)
    fragment_only = {
        "title": "Territory Sales Manager",
        "fit": "low",
        "description": "les and territory management Minimum of 3 years of experience in exterior building materials or construction sales.",
        "body_status": "opened",
    }
    assert "Posting: Minimum of 3 years of experience" in daily_digest._why(fragment_only)
    away = {
        "title": "Sales Engineer",
        "fit": "medium",
        "fit_before_location": "high",
        "location_block": "the card location is Richmond, VA, USA",
        "location": "Richmond, VA, USA",
        "work_mode": "remote",
        "body_status": "opened",
        "description": "Sell the network to local customers.",
    }
    away_why = daily_digest._why(away)
    assert away_why.startswith("Medium. The title score would be high.")
    assert "Richmond, VA, USA" in away_why

    path = tmp_path / "order.docx"
    daily_digest.write_docx(path, "Test", ["Intro"], [trainee, architect, other])
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    assert xml.index("Technical Sales Trainee") < xml.index('w:name="lows"')
    assert xml.index('w:name="lows"') < xml.index("Customer Success Engineer")
    assert xml.index("Solutions Architect") < xml.index('w:name="lows"')


def test_board_shell_is_not_treated_as_read():
    shell = {
        "title": "Technical Sales Engineer - North Central US",
        "fit": "high",
        "company": "Vishay Precision Group",
        "description": "An error occured. {{position.name}} there are currently no open positions.",
        "body_status": "opened",
        "star_notes": [{"label": "business or marketing degree", "quote": "Bachelor's degree in Business"}],
    }
    daily_digest.apply_board_shells([shell])
    assert shell["body_status"] == "shell"
    assert "star_notes" not in shell
    assert shell["fit"] == "low"
    assert shell.get("fit_before_location") is None
    why = daily_digest._why(shell)
    assert "did not render" in why
    assert "no open positions" not in why.lower()
    real = {
        "title": "Technical Sales Trainee",
        "fit": "high",
        "description": "Bachelor's degree in engineering, business, or a technical discipline.",
        "body_status": "opened",
        "star_notes": [{
            "label": "business or marketing degree",
            "quote": "Bachelor's degree in engineering, business, or a technical discipline.",
        }],
    }
    daily_digest.apply_board_shells([real])
    assert real["body_status"] == "opened"
    assert real["star_notes"]


def test_same_oracle_requisition_is_one_card():
    first = {
        "title": "Technical Sales Representative - NM/CO",
        "company": "Emerson Electric",
        "fit": "medium",
        "url": "https://example.com/sites/CX_1/job/26011112",
        "found_on": "2026-10-19",
    }
    second = {
        "title": "Technical Sales Representative - NM/CO",
        "company": "Emerson",
        "fit": "medium",
        "url": "https://example.com/sites/jobsearch/job/26011112",
        "found_on": "2026-10-19",
    }
    other = {
        "title": "Sales Engineer",
        "fit": "high",
        "url": "https://example.com/other",
        "found_on": "2026-10-19",
    }
    listed = daily_digest.listable([first, second, other])
    assert len(listed) == 2
    emerson = [job for job in listed if job["title"].startswith("Technical Sales")]
    assert len(emerson) == 1
    assert emerson[0]["company"] == "Emerson Electric"


def test_locked_docx_does_not_raise(tmp_path):
    target = tmp_path / "open.docx"
    target.write_bytes(b"already open")
    try:
        handle = target.open("r+b")
    except OSError:
        return
    with handle:
        result = daily_digest.write_docx(target, "Test", ["Intro"], [{
            "title": "Sales Engineer",
            "fit": "high",
            "company": "Example",
            "url": "https://example.com/se",
        }])
    if result is None:
        assert target.read_bytes() == b"already open"
        assert not (tmp_path / "open.docx.tmp").exists()
    else:
        assert result == target


def test_merge_keeps_the_original_found_on():
    store = {}
    daily_digest.merge_jobs(store, [{"url": "https://example.com/jobs/1", "title": "A", "fit": "high"}], "2026-10-01")
    daily_digest.merge_jobs(store, [{"url": "https://example.com/jobs/1?utm_source=x", "title": "A again", "fit": "low"}], "2026-10-10")
    assert len(store) == 1
    only = next(iter(store.values()))
    assert only["found_on"] == "2026-10-01"
    assert only["fit"] == "high"


def test_merge_refreshes_unread_text_and_keeps_found_on():
    store = {}
    daily_digest.merge_jobs(store, [{
        "url": "https://example.com/jobs/1",
        "title": "Sales Engineer",
        "fit": "low",
        "description": "",
        "body_status": "unread",
        "fit_blockers": ["The posting text could not be read, so the requirements are unchecked."],
    }], "2026-10-01")
    daily_digest.merge_jobs(store, [{
        "url": "https://example.com/jobs/1",
        "title": "Sales Engineer",
        "fit": "high",
        "description": "Opened posting text. " * 20,
        "body_status": "opened",
        "fit_blockers": [],
        "fit_reasons": ["A sales-engineering role."],
    }], "2026-10-10")
    row = next(iter(store.values()))
    assert row["found_on"] == "2026-10-01"
    assert row["fit"] == "high"
    assert row["body_status"] == "opened"
    assert "Opened posting" in row["description"]
    assert row["found_at"]


def test_regrade_rereads_shell_and_unread_only(monkeypatch):
    import posting_text

    calls = []

    def enrich(jobs, workers=8, only=None):
        calls.extend(job["url"] for job in jobs if only is None or only(job))
        return {}

    monkeypatch.setattr(posting_text, "enrich", enrich)
    jobs = [
        {"url": "https://a", "title": "Sales Engineer", "company": "Acme", "description": "x" * 2000, "body_status": "shell"},
        {"url": "https://b", "title": "Sales Engineer", "company": "Acme", "description": "", "body_status": "unread"},
        {"url": "https://c", "title": "Sales Engineer", "company": "Acme", "description": "y" * 2000, "body_status": "opened"},
        {"url": "https://d", "title": "Senior Solutions Engineer", "company": "Acme", "description": "", "body_status": "shell"},
        {"url": "https://e", "title": "Sales Engineer", "company": "Acme", "description": "z" * 2000},
    ]
    daily_digest.regrade_store(jobs)
    assert calls == ["https://a", "https://b"]


def test_collapse_store_keeps_earliest_found_on_and_longer_text():
    store = {
        "later": {
            "title": "Sales Engineer", "company": "Acme", "location": "Remote",
            "url": "https://jobs.example.com/later", "found_on": "2026-10-05",
            "description": "much longer text",
        },
        "early": {
            "title": "Sales Engineer", "company": "Acme", "location": "Remote",
            "url": "https://jobs.example.com/early", "found_on": "2026-10-01",
            "description": "short",
        },
    }
    assert daily_digest.collapse_store(store) == 1
    row = next(iter(store.values()))
    assert row["url"].endswith("/early")
    assert row["found_on"] == "2026-10-01"
    assert row["description"] == "much longer text"


def test_backfill_does_not_overwrite_an_existing_day_file(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    existing = tmp_path / "2026-10-20.docx"
    existing.write_bytes(b"original")
    store = {
        "a": {
            "found_on": "2026-10-20", "backfilled": True, "title": "Sales Engineer",
            "fit": "high", "url": "https://a.example/1", "company": "Acme",
        },
        "b": {
            "found_on": "2026-10-21", "backfilled": True, "title": "Sales Engineer",
            "fit": "high", "url": "https://b.example/2", "company": "Acme",
        },
    }
    written = daily_digest.write_backfill_files(store)
    assert existing.read_bytes() == b"original"
    assert (tmp_path / "2026-10-21.docx").exists()
    assert written == [tmp_path / "2026-10-21.docx"]
