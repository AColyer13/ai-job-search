"""The whole apply flow with the real Agent, real form code and a real headless Chromium.

ATS pages are local HTML served through Playwright request routing, Telegram and the model are fakes, and the
"submit" posts to nothing. This is the closest a test can get to a live run without sending an application.
"""
import contextlib
import json
import csv
import zipfile
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_agent  # noqa: E402
import apply_answers  # noqa: E402
import applied_log
import daily_digest
import tracking_audit
from test_apply_agent import FakeTelegram, Runner  # noqa: E402
from test_apply_forms import GREENHOUSE, LEVER, ASHBY, CAPTCHA  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")
GH = "https://job-boards.greenhouse.io/acme/jobs/123"
LV = "https://jobs.lever.co/acme/abc-123"
AB = "https://jobs.ashbyhq.com/acme/abc-123"
PAGES = {GH: GREENHOUSE, LV + "/apply": LEVER, AB + "/application": ASHBY}


@pytest.fixture(scope="module")
def chromium():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def env(tmp_path, chromium):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 x")
    pages = dict(PAGES)
    opened = []

    @contextlib.contextmanager
    def browser():
        context = chromium.new_context()
        context.route("**/*", lambda route: route.fulfill(
            status=200 if route.request.url.split("?")[0] in pages else 404, content_type="text/html",
            body=pages.get(route.request.url.split("?")[0], "<html><body>Not found</body></html>")))
        opened.append(context)
        try:
            yield context.new_page()
        finally:
            context.close()

    logged = []
    tg = FakeTelegram()
    agent = apply_agent.Agent(tg, state_path=tmp_path / "s.json", runner=Runner("Tailored answer."),
                              log=lambda job, status, **kw: logged.append((status, kw)) or True, browser=browser,
                              profile={**apply_answers.DEFAULT_PROFILE, "resume_pdf": str(resume)},
                              bank_path=tmp_path / "bank.json", letter_dir=tmp_path / "letters",
                              shot_dir=tmp_path / "shots", assist_launcher=lambda jid: agent.launched.append(jid), auto_submit=False)
    agent.max_questions = 99  # these forms ask two questions; the several-questions rule has its own test
    agent.launched = []  # Chrome on Adam's PC is never opened by a test
    return agent, tg, logged, pages


def add(agent, url, ats):
    job = {"id": apply_agent.job_id(url), "url": url, "title": "Engineer", "company": "Acme", "fit": "high",
           "fit_score": 90, "ats": ats, "description": "Build", "status": "queued", "created": 0, "updated": 0,
           "answers": {}, "fields": {}, "pending": [], "drafts": {}, "asked": None, "ver": 0}
    agent.jobs[job["id"]] = job
    return job


def drive(agent, tg, answer_text="Yes"):
    """Play Adam: answer every question (buttons first, else text) and finally tap Submit."""
    for _ in range(20):
        job = next(iter(agent.jobs.values()))
        if job["status"] == "asking":
            labels = [l for l, _ in tg.last_buttons()]
            pick = next((l for l in labels if l in ("No", "Yes")), None)
            if job["fields"][job["asked"]]["label"].lower().startswith(("do you require", "are you")):
                pick = "No" if "sponsorship" in job["fields"][job["asked"]]["label"].lower() else "Yes"
            if pick:
                tg.tap(pick)
            else:
                tg.events.append({"type": "text", "text": answer_text})
        elif job["status"] == "ready":
            tg.tap("Submit")
        else:
            return job
        for ev in tg.poll():
            agent.handle(ev)
    return next(iter(agent.jobs.values()))


def test_greenhouse_end_to_end(env):
    agent, tg, logged, _ = env
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["status"] == "ready"  # work authorization and sponsorship are standing facts
    assert job["answers"]["do you require sponsorship"] == "No"
    done = drive(agent, tg)
    assert done["status"] == "applied", [t for t, _ in tg.sent][-3:]
    assert logged[0][0] == "applied" and logged[0][1]["resume"].endswith("resume.pdf")
    assert any("Applied" in t for t, _ in tg.sent)
    drafts = [t for t, _ in tg.sent if "Draft 1" in t]
    assert drafts and "Tailored answer." in drafts[0]


def test_lever_end_to_end(env):
    agent, tg, logged, pages = env
    pages[LV + "/apply"] = LEVER.replace("</body>", '<div class="h-captcha" data-sitekey="x"></div></body>')  # Lever draws this container on the page
    job = add(agent, LV, "lever")
    agent.advance()
    assert job["human_click"]  # seen on the page, not assumed from the link
    done = drive(agent, tg, "Python")
    assert done["status"] == "assisting" and agent.launched == [job["id"]] and not logged  # Lever's hCaptcha: Adam clicks
    agent.state_path.with_name(f"apply_assist_{job['id']}.json").write_text(json.dumps({"status": "submitted", "detail": ""}))
    agent.collect_assists()
    assert job["status"] == "applied" and logged[0][0] == "applied"


def test_ashby_end_to_end(env):
    agent, tg, logged, pages = env
    pages[AB + "/application"] = ASHBY.replace("</body>", '<div style="position:fixed;inset:0;background:#fff;z-index:9">check</div></body>')
    job = add(agent, AB, "ashby")
    agent.advance()
    assert not job.get("human_click")  # nothing on the page says robot check until Submit is pressed
    assert drive(agent, tg)["status"] == "assisting" and agent.launched == [job["id"]]  # the site refused the script, so Chrome tries it


def test_captcha_page_is_prepared_and_left_for_adams_click(env):
    agent, tg, logged, pages = env
    pages[GH] = CAPTCHA
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["human_click"] and job["status"] == "ready" and not logged  # filled, then Adam's last click on the robot check
    done = drive(agent, tg)
    assert done["status"] == "assisting" and agent.launched == [job["id"]]


def test_closed_posting_is_handed_off(env):
    agent, tg, logged, pages = env
    pages[GH] = "<html><body><h1>This job is no longer available</h1></body></html>"
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["status"] == "handed_off" and "closed" in logged[0][1]["reason"]


def test_a_number_404_in_a_real_posting_does_not_look_like_a_missing_page(env):
    agent, tg, logged, pages = env
    pages[GH] = GREENHOUSE.replace("<h2>Apply</h2>", "<h2>Apply</h2><p>Salary band 404 to 520 thousand, room 404.</p>")
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["status"] in ("asking", "ready")


def test_page_that_returns_404_is_logged_as_tried_not_recommended(env):
    agent, tg, logged, pages = env
    job = add(agent, "https://job-boards.greenhouse.io/acme/jobs/999", "greenhouse")
    agent.advance()
    assert job["status"] == "handed_off" and logged[0][0] == "attempted"


def test_form_that_changes_after_approval_is_not_submitted(env):
    agent, tg, logged, pages = env
    job = add(agent, GH, "greenhouse")
    agent.advance()
    for _ in range(10):
        if job["status"] != "asking":
            break
        label = job["fields"][job["asked"]]["label"].lower()
        tg.tap("No" if "sponsorship" in label else "Yes")
        for ev in tg.poll():
            agent.handle(ev)
    assert job["status"] == "ready"
    pages[GH] = GREENHOUSE.replace("<p id=\"err\">", '<div><label for="x">Security clearance *</label><input id="x" name="x" required></div><p id="err">')
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert job["status"] == "applied" and logged[0][0] == "applied"


def test_several_standing_questions_are_answered_and_the_job_is_prepared(env):
    agent, tg, logged, pages = env
    agent.ask_adam = False
    agent.max_questions = 2
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["status"] == "ready" and not logged
    assert job["answers"]["do you require sponsorship"] == "No"
    assert job["answers"]["are you legally authorized to work in the united states"] == "Yes"
    assert not any(row for _, rows in tg.sent for r in (rows or []) for row in r if row[1].startswith("a:"))


def records_setup(env, tmp_path, monkeypatch, url, ats):
    """Use production persistence, isolated entirely under the test directory."""
    agent, tg, _, pages = env
    daily = tmp_path / "records"
    daily.mkdir()
    monkeypatch.setattr(daily_digest, "DAILY", daily)
    monkeypatch.setattr(daily_digest, "STORE_PATH", daily / "latest.json")
    monkeypatch.setattr(daily_digest, "APPLIED_LOG_PATH", daily / "applied_for.json")
    monkeypatch.setattr(applied_log, "LOG_PATH", daily / "applied_for.json")
    monkeypatch.setattr(applied_log, "DOC_PATH", daily / "applied_for.docx")
    monkeypatch.setattr(applied_log, "TRACKER_PATH", daily / "tracker.csv")
    job = add(agent, url, ats)
    job.update(title="Sales Engineer", location="Remote", body_status="opened")
    daily_digest.STORE_PATH.write_text(json.dumps({"jobs": {daily_digest._key(url): job}, "sources": []}), encoding="utf-8")
    bank = {key: {"label": key, "answer": value} for key, value in {
        "are you legally authorized to work in the united states": "Yes",
        "do you require sponsorship": "No",
        "what is your favorite programming language": "Python",
    }.items()}
    agent.bank_path.write_text(json.dumps(bank), encoding="utf-8")
    agent.log_write = apply_agent.log_result
    agent.log_entries = applied_log.load_log
    agent.auto_submit, agent.ask_adam = True, False
    return agent, tg, job, daily


def assert_records_agree(agent, job, daily):
    entries = applied_log.load_log()
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry["status"] == "applied" and entry["resume"] == agent.profile["resume_pdf"]
    with applied_log.TRACKER_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["cv_file"] == agent.profile["resume_pdf"]
    assert tracking_audit.audit(entries, agent.jobs, rows) == []
    persisted = json.loads(agent.state_path.read_text(encoding="utf-8"))
    assert persisted[job["id"]]["status"] == "applied"
    for path in (daily / "latest.docx", applied_log.DOC_PATH):
        with zipfile.ZipFile(path) as report:
            xml = report.read("word/document.xml").decode()
        assert "Sales Engineer" in xml and "AI applied" in xml


@pytest.mark.parametrize("url,ats", [(GH, "greenhouse"), (LV, "lever"), (AB, "ashby")])
def test_automatic_application_persists_through_reports_and_tracker(env, tmp_path, monkeypatch, url, ats):
    agent, tg, job, daily = records_setup(env, tmp_path, monkeypatch, url, ats)
    assert agent.advance()
    assert job["status"] == "applied", [text for text, _ in tg.sent][-3:]
    assert_records_agree(agent, job, daily)
    assert agent.queue([job.copy()]) == []
    assert agent.queue([{**job, "url": "https://other.example/same-role"}]) == []
    assert_records_agree(agent, job, daily)


def test_confirmed_handoff_reconciles_without_another_submission(env, tmp_path, monkeypatch):
    agent, _, job, daily = records_setup(env, tmp_path, monkeypatch, AB, "ashby")
    agent.advance()
    job.update(status="handed_off", reason="No confirmation within 20 minutes", submission_unconfirmed=True)
    agent.save()
    monkeypatch.setattr(agent, "submit_job", lambda *a: pytest.fail("must not submit a confirmed job again"))
    assert agent.reconcile() == 1
    assert "submission_unconfirmed" not in job
    assert_records_agree(agent, job, daily)


def test_log_write_retry_restores_complete_records_without_resubmitting(env, tmp_path, monkeypatch):
    agent, _, job, daily = records_setup(env, tmp_path, monkeypatch, AB, "ashby")
    agent.log_write = lambda *a, **kw: False
    agent.advance()
    assert job["status"] == "applied" and job["unlogged"]["status"] == "applied"
    agent.log_write = apply_agent.log_result
    monkeypatch.setattr(agent, "submit_job", lambda *a: pytest.fail("retry must only write records"))
    assert agent.retry_unlogged() == 1
    assert "unlogged" not in job
    assert_records_agree(agent, job, daily)


@pytest.mark.parametrize("kind", ["receipt", "security_code", "wrong_role", "unreadable"])
def test_email_fallback_preserves_evidence_and_prevents_duplicate_applications(env, tmp_path, monkeypatch, kind):
    import time
    from tests.test_mail_codes import FakeBox, message
    agent, _, job, daily = records_setup(env, tmp_path, monkeypatch, AB, "ashby")
    body = {
        "receipt": "We received your application for Sales Engineer at Acme.",
        "security_code": "Thanks for applying for Sales Engineer at Acme. Your security code is 482913.",
        "wrong_role": "We received your application for Software Engineer at Acme.",
        "unreadable": "",
    }[kind]
    raw = message("Ashby <no-reply@ashbyhq.com>", "Application update", body, time.time() - 1)
    lookup = apply_agent.mail_codes.find_confirmation

    def mail(since, company, role=""):
        assert role == "Sales Engineer"
        return lookup(since, company, role=role, creds=("fixture", "fixture"),
                      connect=lambda host: FakeBox([raw], fail_login=kind == "unreadable"))

    monkeypatch.setattr(apply_agent.mail_codes, "find_confirmation", mail)
    monkeypatch.setattr(agent.forms, "submit", lambda *a, **kw: ("unknown", "connection ended before response"))
    agent.advance()
    if kind == "receipt":
        assert_records_agree(agent, job, daily)
    else:
        assert job["status"] == "handed_off" and job["submission_unconfirmed"] is True
        assert next(iter(applied_log.load_log().values()))["status"] != "applied"
        assert not applied_log.TRACKER_PATH.exists()
        assert "second attempt" in job["reason"]
    assert agent.queue([job.copy()]) == []
