"""Prune rules for daily/latest.txt. No network."""
import pytest
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


def test_grade_blurb_says_why_and_lows_are_left_out(tmp_path):
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
    assert "Technical Sales Trainee" in xml
    assert "Solutions Architect" in xml
    assert "Customer Success Engineer" not in xml
    assert 'w:name="lows"' not in xml
    assert daily_digest.left_out_count([trainee, architect, other]) == 1
    assert [j["title"] for j in daily_digest.fit_jobs([trainee, architect, other])] == [
        "Technical Sales Trainee (Minneapolis)",
        "Solutions Architect",
    ]


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


def test_minimums_line_says_yes_stretch_not_checked_or_no():
    line = daily_digest._minimums_line
    assert line({"minimums": "yes"}) == "Meets the posting's stated minimums: yes."
    assert line({"minimums": "stretch", "minimums_note": "It asks for 2 years."}).startswith("Meets the posting's stated minimums: stretch. It asks")
    assert "not checked" in line({"minimums": "unchecked", "minimums_note": "Text unread."})
    assert line({"minimums": "no", "minimums_note": "Needs 6+ years."}).endswith("no. Needs 6+ years.")
    assert line({}) == ""


def test_apply_link_prefers_the_employers_page():
    assert daily_digest.apply_link({"url": "https://himalayas.app/x", "apply_url": "https://jobs.lever.co/x"}) == "https://jobs.lever.co/x"
    assert daily_digest.apply_link({"url": "https://jobs.lever.co/y"}) == "https://jobs.lever.co/y"
    assert daily_digest.apply_link({}) == ""


# --- Unread snippet rows: board rows whose posting text was never read are withheld -------------
def _row(title, url, fit="medium", minimums="unchecked", **extra):
    return dict({"title": title, "company": "Acme", "location": "Remote (United States)", "url": url, "fit": fit,
                 "minimums": minimums, "found_on": "2026-10-27"}, **extra)


JOOBLE = "https://jooble.org/desc/111"
UNREAD = _row("Sales Engineer", JOOBLE)
READ = _row("Solutions Engineer", "https://boards.greenhouse.io/acme/jobs/1", minimums="yes")
LOW = _row("Solutions Consultant", "https://jooble.org/desc/222", fit="low", minimums="no")


def test_only_unread_snippet_board_rows_are_unchecked():
    assert daily_digest.is_unchecked(UNREAD)
    assert not daily_digest.is_unchecked(dict(UNREAD, minimums="yes"))  # the employer page was read
    assert not daily_digest.is_unchecked(dict(UNREAD, url="https://boards.greenhouse.io/acme/jobs/9"))
    assert not daily_digest.is_unchecked(LOW)


def test_unread_snippet_rows_are_withheld_and_counted_as_left_out():
    rows = [UNREAD, READ, LOW]
    shown, left_out = daily_digest.presentable_split(rows)
    assert [j["title"] for j in shown] == ["Solutions Engineer"]
    assert left_out == []
    assert [j["title"] for j in daily_digest.fit_jobs(rows)] == ["Solutions Engineer"]
    assert daily_digest.left_out_count(rows) == 2  # the low row and the unread Jooble row
    upper = daily_digest.type_sections(daily_digest.fit_jobs(rows))
    assert [j["title"] for s in upper for j in s["jobs"]] == ["Solutions Engineer"]


def test_word_file_does_not_list_unread_snippet_rows(tmp_path):
    import zipfile

    path = tmp_path / "withheld.docx"
    daily_digest.write_docx(path, "Test", ["Intro"], daily_digest.fit_jobs([UNREAD, READ]))
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    assert "Solutions Engineer" in xml
    assert "Sales Engineer" not in xml
    assert "jooble.org" not in zipfile.ZipFile(path).read("word/_rels/document.xml.rels").decode("utf-8")


def test_publish_writes_only_the_two_word_files():
    import inspect

    assert "html_report" not in inspect.getsource(daily_digest)
    assert "dashboard" not in inspect.getsource(daily_digest._publish).lower()


def test_the_daily_list_shows_a_green_check_or_a_red_x_from_the_applied_log():
    job = {"title": "Solutions Engineer", "company": "Acme", "url": "https://jobs.acme.com/se-1", "fit": "high", "location": "Remote"}
    other = {"title": "QA Engineer", "company": "Beta", "url": "https://jobs.beta.com/qa-2", "fit": "high", "location": "Remote"}
    fresh = {"title": "Support", "company": "Gamma", "url": "https://jobs.gamma.com/s-3", "fit": "high", "location": "Remote"}
    entries = {
        daily_digest._key(job["url"]): {"status": "applied", "applied_by": "Adam", "date_applied": "2026-10-30", "url": job["url"]},
        daily_digest._key(other["url"]): {"status": "attempted", "reason": "robot check on the form", "url": other["url"]},
    }
    assert daily_digest.result_mark(job, entries) == ("✅", "Adam applied on 2026-10-30")
    mark, text = daily_digest.result_mark(other, entries)
    assert mark == "❌" and text.startswith("AI didn't apply, blocked") and "robot check" in text
    assert daily_digest.result_mark(fresh, entries) is None  # nobody has tried it yet
    xml = "".join(daily_digest._job_blocks(other, None, 1, entries))
    assert "❌" in xml and 'w:val="C00000"' in xml


@pytest.mark.parametrize("reason,kind", [
    ("too many unique questions (3): a; b; c", "blocked"),
    ("a robot check (Cloudflare or similar) blocked the page; the agent does not get around robot checks", "blocked"),
    ("no application form found on the page (account or login required)", "blocked"),
    ("the project has no answer yet for: describe your account setup", "gap"),
    ("no application form found on the page", "gap"),
    ("Clicked Submit, never saw a confirmation page, never received an email. AI tried; Adam needs to apply.", "gap"),
    ("This posting is closed or gone", "closed"),
    ("Adam: a required GPA means move on from the job", "closed"),
    ("Something broke before the result was known (RuntimeError).", "gap"),
    ("No confirmation within 20 minutes, so I stopped waiting.", "gap"),
    ("Only via Handshake with school alumni access", "other"),
    ("", "other"),
])
def test_not_applied_reasons_are_sorted_into_blocked_gap_or_closed(reason, kind):
    assert daily_digest.not_applied_kind(reason) == kind
