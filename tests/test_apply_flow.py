"""The whole apply flow with the real Agent, real form code and a real headless Chromium.

ATS pages are local HTML served through Playwright request routing, Telegram and the model are fakes, and the
"submit" posts to nothing. This is the closest a test can get to a live run without sending an application.
"""
import contextlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_agent  # noqa: E402
import apply_answers  # noqa: E402
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
    assert job["status"] == "asking"  # work authorization and sponsorship are never guessed
    done = drive(agent, tg)
    assert done["status"] == "applied", [t for t, _ in tg.sent][-3:]
    assert logged[0][0] == "applied" and logged[0][1]["resume"].endswith("resume.pdf")
    assert any("Applied" in t for t, _ in tg.sent)
    drafts = [t for t, _ in tg.sent if "Draft 1" in t]
    assert drafts and "Tailored answer." in drafts[0]


def test_lever_end_to_end(env):
    agent, tg, logged, _ = env
    job = add(agent, LV, "lever")
    agent.advance()
    done = drive(agent, tg, "Python")
    assert done["status"] == "assisting" and agent.launched == [job["id"]] and not logged  # Lever's hCaptcha: Adam clicks
    agent.state_path.with_name(f"apply_assist_{job['id']}.json").write_text(json.dumps({"status": "submitted", "detail": ""}))
    agent.collect_assists()
    assert job["status"] == "applied" and logged[0][0] == "applied"


def test_ashby_end_to_end(env):
    agent, tg, logged, _ = env
    job = add(agent, AB, "ashby")
    agent.advance()
    assert drive(agent, tg)["status"] == "assisting" and agent.launched == [job["id"]]


def test_captcha_page_is_prepared_and_left_for_adams_click(env):
    agent, tg, logged, pages = env
    pages[GH] = CAPTCHA
    job = add(agent, GH, "greenhouse")
    agent.advance()
    assert job["human_click"] and job["status"] == "asking" and not logged  # the agent still does everything but the click
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
    assert job["status"] == "handed_off" and logged[0][0] == "attempted" and "clearance" in logged[0][1]["reason"].lower()
