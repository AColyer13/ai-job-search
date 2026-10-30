"""The apply log: statuses, monthly grouping, and the Word file. No network."""
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import applied_log  # noqa: E402

STORE = {
    "https://boards.example.com/acme/1": {
        "title": "Solutions Engineer",
        "company": "Acme",
        "location": "Remote, US",
        "work_mode": "remote",
        "fit": "high",
        "fit_score": 82,
        "url": "https://boards.example.com/acme/1",
        "apply_url": "https://jobs.acme.com/se-1",
    },
    "https://boards.example.com/beta/2": {
        "title": "Junior Software Engineer",
        "company": "Beta",
        "location": "Edina, MN",
        "fit": "medium",
        "fit_score": 61,
        "url": "https://boards.example.com/beta/2",
    },
}


def add(entries, url, status, day, **kw):
    return applied_log.add_entry(entries, url=url, status=status, day=day, store=STORE, **kw)


def test_card_data_comes_from_the_daily_list_and_the_employer_link_wins():
    entries = {}
    key, _ = add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-27", method="Greenhouse form")
    entry = entries[key]
    assert (entry["title"], entry["company"], entry["fit"], entry["fit_score"]) == ("Solutions Engineer", "Acme", "high", 82)
    assert entry["url"] == "https://jobs.acme.com/se-1"
    assert entry["applied_by"] == "AI" and entry["date_applied"] == "2026-10-27"


def test_the_employer_link_finds_the_same_card():
    entries = {}
    add(entries, "https://jobs.acme.com/se-1", "recommended", "2026-10-27", reason="Needs an account")
    (entry,) = entries.values()
    assert entry["title"] == "Solutions Engineer"


def test_unknown_url_needs_a_title_and_company():
    with pytest.raises(applied_log.LogError):
        add({}, "https://elsewhere.example.com/job/9", "applied", "2026-10-27")
    entries = {}
    add(entries, "https://elsewhere.example.com/job/9", "applied", "2026-10-27", title="Dev", company="Gamma")
    assert len(entries) == 1


def test_same_job_under_another_link_is_one_entry():
    entries = {}
    add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-27")
    _, message = add(entries, "https://linkedin.example.com/jobs/77", "applied", "2026-10-28",
                     title="Solutions Engineer", company="Acme", location="Remote")
    assert len(entries) == 1
    assert "already logged as applied on 2026-10-27" in message


def test_recommended_moves_to_applied_but_never_back():
    entries = {}
    url = "https://boards.example.com/beta/2"
    add(entries, url, "recommended", "2026-10-30", reason="CAPTCHA")
    add(entries, url, "applied", "2026-11-02", by="Adam")
    (entry,) = entries.values()
    assert (entry["status"], entry["applied_by"], entry["date_applied"], entry["date_recommended"]) == (
        "applied", "Adam", "2026-11-02", "2026-10-30")
    _, message = add(entries, url, "recommended", "2026-11-03")
    assert entry["status"] == "applied" and "not moved back" in message


def test_months_group_newest_first_and_use_the_right_date():
    entries = {}
    add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-27")
    add(entries, "https://boards.example.com/beta/2", "recommended", "2026-11-03", reason="Login wall")
    grouped = applied_log.months(entries)
    assert list(grouped) == ["2026-11", "2026-10"]
    assert [e["company"] for e in grouped["2026-10"]["applied"]] == ["Acme"]
    assert [e["company"] for e in grouped["2026-11"]["recommended"]] == ["Beta"]
    assert applied_log.month_label("2026-11") == "November 2026"


def test_bad_inputs_are_refused():
    with pytest.raises(applied_log.LogError):
        add({}, "https://boards.example.com/acme/1", "interviewing", "2026-10-27")
    with pytest.raises(ValueError):
        add({}, "https://boards.example.com/acme/1", "applied", "10/27/2026")


def test_a_damaged_log_is_not_overwritten(tmp_path):
    path = tmp_path / "applied_for.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(applied_log.LogError):
        applied_log.load_log(path)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_log_round_trips(tmp_path):
    path = tmp_path / "applied_for.json"
    entries = {}
    add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-27")
    applied_log.save_log(entries, path)
    assert applied_log.load_log(path) == entries
    assert json.loads(path.read_text(encoding="utf-8"))["entries"]


def _document_xml(path):
    with zipfile.ZipFile(path) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_word_file_lists_only_applied_jobs_newest_month_first(tmp_path):
    entries = {}
    add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-27", method="Greenhouse form")
    add(entries, "https://boards.example.com/beta/2", "recommended", "2026-11-03", reason="Needs an account")
    add(entries, "https://boards.example.com/delta/4", "attempted", "2026-11-03", reason="Robot check", title="Dev", company="Delta")
    path = applied_log.write_doc(entries, tmp_path / "applied_for.docx", today="2026-11-03")
    xml = _document_xml(path)
    ElementTree.fromstring(xml)  # well-formed
    for text in ("October 2026", "AI applied on 2026-10-27", "1 applied: 1 AI applied, 0 Adam applied"):
        assert text in xml
    for text in ("November 2026", "Needs an account", "Robot check", "by hand"):
        assert text not in xml  # a job nobody has applied for is not on the list
    with zipfile.ZipFile(path) as zf:
        assert "https://jobs.acme.com/se-1" in zf.read("word/_rels/document.xml.rels").decode("utf-8")


def test_empty_log_still_writes_a_valid_file(tmp_path):
    path = applied_log.write_doc({}, tmp_path / "applied_for.docx", today="2026-10-27")
    xml = _document_xml(path)
    ElementTree.fromstring(xml)
    assert "Nothing logged yet." in xml


def _applied_entry(**over):
    entry = {"title": "Solutions Engineer", "company": "Acme", "url": "https://jobs.acme.com/se-1", "fit": "high",
             "applied_by": "AI", "date_applied": "2026-10-27", "method": "Greenhouse form"}
    entry.update(over)
    return entry


def test_tracker_row_is_appended_without_touching_existing_rows(tmp_path):
    path = tmp_path / "job_search_tracker.csv"
    header = ",".join(applied_log.TRACKER_HEADER)
    old = f"{header}\r\n2026-10-20,Boomi,Software,Presales Solutions Engineer,presales,greenhouse,applied,,high,note,,,https://x\r\n"
    path.write_bytes(old.encode("utf-8"))
    assert applied_log.append_tracker_row(_applied_entry(), path) == "added"
    data = path.read_bytes().decode("utf-8")
    assert data.startswith(old)
    new_row = data[len(old):]
    assert new_row.endswith("\r\n") and new_row.count("\r\n") == 1
    cells = new_row.strip().split(",")
    assert cells[:2] == ["2026-10-27", "Acme"]
    assert "sales engineering" in cells and "applied" in cells and "https://jobs.acme.com/se-1" in cells


def test_tracker_skips_a_company_and_role_already_there(tmp_path):
    path = tmp_path / "job_search_tracker.csv"
    applied_log.append_tracker_row(_applied_entry(), path)
    before = path.read_bytes()
    assert applied_log.append_tracker_row(_applied_entry(url="https://other.example.com/9"), path) == "already in the tracker"
    assert path.read_bytes() == before


def test_missing_tracker_is_created_with_the_standard_header(tmp_path):
    path = tmp_path / "job_search_tracker.csv"
    applied_log.append_tracker_row(_applied_entry(), path)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == ",".join(applied_log.TRACKER_HEADER)


def test_submitted_files_go_into_the_tracker_columns(tmp_path):
    path = tmp_path / "job_search_tracker.csv"
    applied_log.append_tracker_row(_applied_entry(resume=r"C:\resumes\Adam_Colyer_Resume.pdf", cover_letter="cover.pdf"), path)
    import csv as _csv
    (row,) = list(_csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert row["cv_file"] == r"C:\resumes\Adam_Colyer_Resume.pdf" and row["cover_letter_file"] == "cover.pdf"
    assert "CV" not in row["notes"]
    applied_log.append_tracker_row(_applied_entry(company="Other", url="https://jobs.other.com/1"), path)
    rows = list(_csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert rows[1]["cv_file"] == "" and "CV, cover letter not recorded" in rows[1]["notes"]


def test_migrate_adds_yield_columns_and_leaves_existing_cells(tmp_path):
    path = tmp_path / "job_search_tracker.csv"
    old = applied_log.TRACKER_HEADER[:13]
    path.write_text(
        ",".join(old) + "\n"
        "2026-10-27,Acme,,Sales Engineer,sales engineering,ashby form,applied,,high,"
        "Applied by AI on 2026-10-27 via Ashby form.,Resume_v2.pdf,,https://jobs.example.com/1\n",
        encoding="utf-8",
    )
    assert applied_log.migrate_tracker(path) == 1
    first, row = path.read_text(encoding="utf-8").splitlines()
    assert first == ",".join(applied_log.TRACKER_HEADER)
    cells = dict(zip(applied_log.TRACKER_HEADER, row.split(",")))
    assert cells["company"] == "Acme" and cells["cv_file"] == "Resume_v2.pdf"
    assert cells["apply_mode"] == "ai"
    assert cells["resume_version"].startswith("Resume_v2")
    assert cells["first_response_date"] == ""
    assert applied_log.migrate_tracker(path) == 0  # second run changes nothing


def test_new_tracker_rows_carry_score_source_and_mode(tmp_path):
    import csv

    path = tmp_path / "job_search_tracker.csv"
    entry = _applied_entry(fit_score=0, discovery_source="himalayas.app", score_version="abc1234", applied_by="Adam")
    applied_log.append_tracker_row(entry, path)
    applied_log.append_tracker_row(_applied_entry(company="Other", url="https://jobs.other.com/2", fit_score=None), path)
    first, second = csv.DictReader(path.open(encoding="utf-8", newline=""))
    assert (first["score"], first["discovery_source"], first["score_version"], first["apply_mode"]) == ("0", "himalayas.app", "abc1234", "hand")
    assert second["score"] == "" and second["apply_mode"] == "ai"


def test_resume_version_hashes_the_submitted_file(tmp_path):
    pdf = tmp_path / "Resume_2026-10.pdf"
    pdf.write_bytes(b"first build")
    first = applied_log.resume_version(str(pdf))
    pdf.write_bytes(b"rebuilt under the same name")
    second = applied_log.resume_version(str(pdf))
    assert first.startswith("Resume_2026-10@") and second.startswith("Resume_2026-10@") and first != second
    assert applied_log.resume_version(str(pdf), hash_file=False) == "Resume_2026-10"
    assert applied_log.resume_version(str(tmp_path / "missing.pdf")) == "missing"
    assert applied_log.resume_version("") == ""


def test_unsent_is_its_own_bucket_and_applying_moves_it_to_applied():
    entries = {}
    url = "https://boards.example.com/acme/1"
    add(entries, url, "unsent", "2026-10-28", reason="No Submit tap in 4.5 hours")
    (entry,) = entries.values()
    assert entry["status"] == "unsent" and entry["date_unsent"] == "2026-10-28"
    bucket = applied_log.months(entries)["2026-10"]
    assert len(bucket["unsent"]) == 1 and not bucket["recommended"] and not bucket["applied"]
    # a later recommended entry for the same job does not push it back
    _, message = add(entries, url, "recommended", "2026-10-29", reason="x")
    assert entry["status"] == "unsent" and "left as is" in message
    # whoever applies later moves it to Applied
    _, message = add(entries, url, "applied", "2026-10-30", by="AI", method="Greenhouse form")
    assert entry["status"] == "applied" and entry["date_applied"] == "2026-10-30" and len(entries) == 1
    _, message = add(entries, url, "unsent", "2026-10-31")
    assert entry["status"] == "applied" and "not moved back" in message


def test_a_recommended_job_can_become_unsent():
    entries = {}
    url = "https://boards.example.com/beta/2"
    add(entries, url, "recommended", "2026-10-27", reason="Needs an account")
    add(entries, url, "unsent", "2026-10-28", reason="No tap")
    (entry,) = entries.values()
    assert entry["status"] == "unsent" and entry["date_recommended"] == "2026-10-27"


def test_an_email_confirmed_application_by_adam_is_marked_in_the_list(tmp_path):
    entries = {}
    add(entries, "https://boards.example.com/acme/1", "attempted", "2026-10-28", reason="Robot check")
    add(entries, "https://boards.example.com/acme/1", "applied", "2026-10-30", by="Adam")
    (entry,) = entries.values()
    entry["email_confirmed"] = True
    xml = _document_xml(applied_log.write_doc(entries, tmp_path / "applied_for.docx", today="2026-10-30"))
    assert "Adam applied on 2026-10-30 (confirmed in Gmail)" in xml and "0 AI applied, 1 Adam applied" in xml


def _tracker_with(tmp_path, *rows):
    path = tmp_path / "tracker.csv"
    for n, row in enumerate(rows):
        applied_log.append_tracker_row(_applied_entry(url=f"https://jobs.example.com/{n}", **row), path=path)
    return path


def _row(path, company):
    import csv
    return next(r for r in csv.DictReader(path.open(encoding="utf-8-sig")) if r["company"] == company)


def test_status_moves_one_row_and_fills_the_first_reply_once(tmp_path):
    path = _tracker_with(tmp_path, {"company": "Acme", "title": "Backend Engineer"}, {"company": "Beta", "title": "Backend Engineer"})
    applied_log.set_tracker_status(company="Acme", to="interview", reply_date="2026-10-02", note="phone screen", path=path)
    applied_log.set_tracker_status(company="Acme", to="interview", reply_date="2026-10-09", note="second round", path=path)
    acme = _row(path, "Acme")
    assert acme["status"] == "interview"
    assert acme["first_response_date"] == "2026-10-02"  # never overwritten
    assert "2026-10-02: phone screen" in acme["notes"] and "2026-10-09: second round" in acme["notes"]
    assert _row(path, "Beta")["status"] == "applied"


def test_status_is_idempotent_and_does_not_repeat_a_note(tmp_path):
    path = _tracker_with(tmp_path, {"company": "Acme", "title": "Backend Engineer"})
    kw = dict(company="Acme", to="rejected", reply_date="2026-10-02", note="no thanks", path=path)
    applied_log.set_tracker_status(**kw)
    assert "nothing to change" in applied_log.set_tracker_status(**kw)
    assert _row(path, "Acme")["notes"].count("no thanks") == 1


def test_status_refuses_hired_a_closed_row_and_an_ambiguous_match(tmp_path):
    path = _tracker_with(tmp_path, {"company": "Acme", "title": "Backend Engineer"}, {"company": "Acme", "title": "Data Engineer"})
    with pytest.raises(applied_log.LogError):
        applied_log.set_tracker_status(company="Acme", role="Backend", to="hired", path=path)
    with pytest.raises(applied_log.LogError):
        applied_log.set_tracker_status(company="Acme", to="interview", path=path)  # two Acme rows
    applied_log.set_tracker_status(company="Acme", role="Backend", to="rejected", reply_date="2026-10-02", path=path)
    with pytest.raises(applied_log.LogError):
        applied_log.set_tracker_status(company="Acme", role="Backend", to="interview", path=path)  # closed
    with pytest.raises(applied_log.LogError):
        applied_log.set_tracker_status(company="Nobody", to="interview", path=path)


def test_a_later_call_fills_the_resume_into_an_existing_tracker_row(tmp_path):
    import csv as _csv
    path = tmp_path / "job_search_tracker.csv"
    applied_log.append_tracker_row(_applied_entry(), path)  # logged first by email sync, no resume known
    entry = _applied_entry(resume=r"C:\resumes\Adam_Colyer_Resume_2026-10.pdf", cover_letter="cover.pdf")
    assert applied_log.append_tracker_row(entry, path) == "already in the tracker"
    assert applied_log.fill_tracker_blanks(entry, path) == "filled cv_file, cover_letter_file, resume_version"
    (row,) = list(_csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert row["cv_file"] == r"C:\resumes\Adam_Colyer_Resume_2026-10.pdf" and row["cover_letter_file"] == "cover.pdf"
    # a filled cell is never overwritten, and a second call changes nothing
    other = _applied_entry(resume=r"C:\resumes\Other.pdf")
    assert applied_log.fill_tracker_blanks(other, path) == "nothing to fill"
    (row,) = list(_csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert row["cv_file"].endswith("Adam_Colyer_Resume_2026-10.pdf")


def test_attempted_is_its_own_bucket_and_applied_moves_it_out():
    entries = {}
    url = "https://boards.greenhouse.io/x/jobs/1"
    key, msg = applied_log.add_entry(entries, url=url, title="T", company="C", status="attempted", day="2026-10-29",
                                     reason="email verification code: no code arrived")
    assert entries[key]["status"] == "attempted" and "not applied" in msg
    assert applied_log.months(entries)["2026-10"]["attempted"][0]["reason"].startswith("email verification code")
    applied_log.add_entry(entries, url=url, title="T", company="C", status="unsent", day="2026-10-30")  # never downgrades it
    assert entries[key]["status"] == "attempted"
    applied_log.add_entry(entries, url=url, title="T", company="C", status="applied", by="Adam", day="2026-10-30")
    assert entries[key]["status"] == "applied"
    _, msg = applied_log.add_entry(entries, url=url, title="T", company="C", status="attempted", day="2026-10-31")
    assert entries[key]["status"] == "applied" and "already applied" in msg  # an applied job never goes back
