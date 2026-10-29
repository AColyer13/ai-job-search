"""The apply agent's state machine with a fake Telegram, fake forms and no browser or model.

The rule under test: nothing is submitted until Adam taps Submit on the current summary.
"""
import contextlib
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import apply_agent  # noqa: E402
import apply_answers  # noqa: E402
import apply_forms  # noqa: E402

URL = "https://job-boards.greenhouse.io/acme/jobs/123"


class FakeTelegram:
    def __init__(self):
        self.sent, self.events, self.answers = [], [], []

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return len(self.sent)

    def poll(self, wait=0):
        events, self.events = self.events, []
        return events

    def answer(self, callback_id, text="Got it"):
        self.answers.append(text)

    def last_buttons(self):
        for text, buttons in reversed(self.sent):
            if buttons:
                return [b for row in buttons for b in row]
        return []

    def tap(self, label_start):
        data = next(d for label, d in self.last_buttons() if label.startswith(label_start))
        self.events.append({"type": "button", "data": data, "callback_id": "cb"})
        return data


def field(key, kind="text", label=None, required=True, options=(), **kw):
    return apply_forms.Field(kind=kind, label=label or key, key=key, required=required, options=list(options), **kw)


class FakeForms:
    FormError = apply_forms.FormError
    best_option = staticmethod(apply_forms.best_option)
    detect_ats = staticmethod(apply_forms.detect_ats)
    clean_url = staticmethod(apply_forms.clean_url)
    application_url = staticmethod(apply_forms.application_url)

    def __init__(self, fields, blocker="", submit_result=("submitted", "")):
        self.fields, self.blocker, self.submit_result = fields, blocker, submit_result
        self.filled, self.submitted, self.pdfs = [], 0, []
        self.fill_errors = {}

    def discover(self, page, url):
        return apply_forms.Form(ats="greenhouse", url=url, fields=list(self.fields), blocker=self.blocker)

    def fill(self, page, form, answers):
        self.filled.append(dict(answers))
        return dict(self.fill_errors)

    def submit(self, page):
        self.submitted += 1
        return self.submit_result

    def screenshot(self, page, path):
        return str(path)

    def render_pdf(self, page, path, *a):
        self.pdfs.append(str(path))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF")


@contextlib.contextmanager
def fake_browser():
    yield SimpleNamespace()


class Runner:
    def __init__(self, text="A short draft.", ok=True):
        self.text, self.ok, self.calls = text, ok, 0

    def __call__(self, prompt, **kw):
        self.calls += 1
        return SimpleNamespace(ok=self.ok, text=self.text, provider="claude" if self.ok else "",
                               attempts=[] if self.ok else [("claude", "limit"), ("grok", "402")])


@pytest.fixture
def env(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    profile = {**apply_answers.DEFAULT_PROFILE, "resume_pdf": str(resume)}
    logged = []
    return SimpleNamespace(tmp=tmp_path, profile=profile, logged=logged, entries={}, fits={},
                           log=lambda job, status, **kw: logged.append((status, kw)) or True)


def make_agent(env, fields, runner=None, **forms_kw):
    forms = FakeForms(fields, **forms_kw)
    tg = FakeTelegram()
    agent = apply_agent.Agent(tg, state_path=env.tmp / "state.json", forms=forms, runner=runner or Runner(),
                              log=env.log, browser=fake_browser, profile=env.profile,
                              bank_path=env.tmp / "bank.json", letter_dir=env.tmp / "letters",
                              shot_dir=env.tmp / "shots", log_entries=lambda: env.entries,
                              fit_of=lambda url: env.fits.get(url))
    job = {"id": "j1", "url": URL, "title": "Engineer", "company": "Acme", "fit": "high", "fit_score": 90,
           "ats": "greenhouse", "description": "Build things", "status": "queued", "created": 0, "updated": 0,
           "answers": {}, "fields": {}, "pending": [], "drafts": {}, "asked": None, "ver": 0}
    agent.jobs["j1"] = job
    return agent, tg, forms, job


BASIC = [field("first name"), field("last name"), field("email"), field("resume cv", "file", "Resume/CV")]


def test_nothing_is_submitted_before_the_tap(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    assert job["status"] == "ready" and forms.submitted == 0 and forms.filled == []
    agent.serve(minutes=0.0001, wait=0)
    assert forms.submitted == 0 and not env.logged


def test_tap_submit_fills_submits_and_logs_applied(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    tg.tap("Submit")
    for ev in tg.poll():
        agent.handle(ev)
    assert forms.submitted == 1 and job["status"] == "applied"
    assert forms.filled[0]["first name"] == "Adam" and forms.filled[0]["resume cv"].endswith("resume.pdf")
    assert env.logged[0][0] == "applied" and env.logged[0][1]["resume"] == env.profile["resume_pdf"]


def test_stale_submit_button_is_refused_after_a_draft_edit(env):
    fields = BASIC + [field("why do you want to work here", "textarea", "Why do you want to work here?")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    old = tg.tap("Submit")
    tg.poll()
    agent.on_text("My own answer.")
    assert job["ver"] == 2 and job["answers"]["why do you want to work here"] == "My own answer."
    agent.handle({"type": "button", "data": old, "callback_id": "cb"})
    assert forms.submitted == 0 and "out of date" in tg.answers[-1]
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert forms.submitted == 1


def test_question_with_options_uses_buttons_and_saves_the_answer(env):
    fields = BASIC + [field("are you authorized to work in the us", "radio", "Are you authorized to work in the US?",
                            options=["Yes", "No"])]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert job["status"] == "asking"
    assert [l for l, _ in tg.last_buttons()][:2] == ["Yes", "No"]
    tg.tap("Yes")
    agent.handle(tg.poll()[0])
    assert job["status"] == "ready" and job["answers"]["are you authorized to work in the us"] == "Yes"
    assert "are you authorized to work in the us" in apply_answers.load_bank(env.tmp / "bank.json")


def test_typed_answer_is_matched_to_an_option(env):
    fields = BASIC + [field("degree", "select", "Degree", options=["Bachelor's Degree", "Master's Degree"])]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    agent.on_text("masters")
    assert "does not match" in tg.sent[-1][0] or job["status"] == "asking"
    agent.on_text("Master's Degree")
    assert job["answers"]["degree"] == "Master's Degree"


def test_free_text_question_takes_a_reply_and_is_not_saved_when_job_specific(env):
    fields = BASIC + [field("desired salary", "text", "Desired salary")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    agent.on_text("120000")
    assert job["answers"]["desired salary"] == "120000" and job["status"] == "ready"
    assert "desired salary" not in apply_answers.load_bank(env.tmp / "bank.json")


def test_cover_letter_file_is_drafted_rendered_and_attached(env):
    fields = BASIC + [field("cover letter", "file", "Cover Letter")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert forms.pdfs and job["answers"]["cover letter"].endswith("apply_j1.pdf")
    assert any("Draft 1" in t for t, _ in tg.sent)
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert env.logged[0][1]["cover_letter"].endswith("apply_j1.pdf")


def test_no_ai_provider_hands_the_job_off_without_guessing(env):
    fields = BASIC + [field("cover letter", "file", "Cover Letter")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner(ok=False))
    agent.advance()
    assert job["status"] == "handed_off" and env.logged[0][0] == "recommended"
    assert "no AI provider" in env.logged[0][1]["reason"] and forms.submitted == 0


def test_needs_adam_draft_turns_into_a_question(env):
    fields = BASIC + [field("why do you want to work here", "textarea", "Why do you want to work here?")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner(text="NEEDS_ADAM"))
    agent.advance()
    assert job["status"] == "asking" and job["asked"] == "why do you want to work here"


@pytest.mark.parametrize("blocker", ["a CAPTCHA is on the page", "sign in required"])
def test_blocker_hands_off_to_recommended(env, blocker):
    agent, tg, forms, job = make_agent(env, BASIC, blocker=blocker)
    agent.advance()
    assert job["status"] == "handed_off" and env.logged[0][0] == "recommended" and forms.submitted == 0


def test_form_error_hands_off(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    forms.discover = lambda page, url: (_ for _ in ()).throw(apply_forms.FormError("the posting is closed or gone"))
    agent.advance()
    assert env.logged == [("recommended", {"reason": "the posting is closed or gone"})]


def test_missing_resume_file_hands_off(env):
    env.profile["resume_pdf"] = str(env.tmp / "nope.pdf")
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    assert job["status"] == "handed_off" and "resume PDF is missing" in env.logged[0][1]["reason"]


def test_required_checkbox_consent_is_ticked_only_at_submit(env):
    fields = BASIC + [field("i agree to the privacy policy", "checkbox", "I agree to the privacy policy")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert job["answers"]["i agree to the privacy policy"] is True and forms.filled == []


def test_form_that_gains_a_required_field_after_approval_is_not_submitted(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    forms.fields = BASIC + [field("security clearance", "text", "Security clearance")]
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert forms.submitted == 0 and job["status"] == "handed_off" and "Security clearance" in env.logged[0][1]["reason"]


def test_required_field_that_fails_to_fill_is_not_submitted(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    forms.fill_errors = {"email": "could not type"}
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert forms.submitted == 0 and env.logged[0][0] == "recommended"


def test_dry_run_fills_but_never_submits_or_logs(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.dry_run = True
    agent.advance()
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert forms.filled and forms.submitted == 0 and job["status"] == "dry_run" and not env.logged


@pytest.mark.parametrize("result,status", [(("unknown", "no confirmation"), "handed_off"),
                                           (("invalid", "Email is required"), "handed_off"),
                                           (("blocked", "CAPTCHA"), "handed_off")])
def test_unconfirmed_submit_is_never_logged_as_applied(env, result, status):
    agent, tg, forms, job = make_agent(env, BASIC, submit_result=result)
    agent.advance()
    tg.tap("Submit")
    agent.handle(tg.poll()[0])
    assert job["status"] == status and [s for s, _ in env.logged] == ["recommended"]


def test_skip_and_i_will_do_it(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    tg.tap("Skip")
    agent.handle(tg.poll()[0])
    assert job["status"] == "skipped" and not env.logged
    agent2, tg2, _, job2 = make_agent(env, BASIC)
    agent2.advance()
    tg2.tap("I'll do it")
    agent2.handle(tg2.poll()[0])
    assert job2["status"] == "handed_off" and env.logged[0][0] == "recommended"


def test_a_summary_waiting_for_submit_does_not_hold_up_the_next_job(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    second = dict(job, id="j2", url=URL + "4", status="queued", answers={}, fields={}, drafts={}, pending=[])
    agent.jobs["j2"] = second
    assert agent.advance() is True and job["status"] == "ready" and second["status"] == "queued"
    assert agent.advance() is True and second["status"] == "ready"
    first_submit = [d for l, d in tg.last_buttons() if l == "Submit"]
    assert first_submit and first_submit[0].startswith("s:j2:")


def test_an_unanswered_question_holds_the_line_so_replies_match_one_question(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("desired salary", "text", "Desired salary")])
    second = dict(job, id="j2", url=URL + "4", status="queued", answers={}, fields={}, drafts={}, pending=[])
    agent.jobs["j2"] = second
    assert agent.advance() is True and job["status"] == "asking"
    assert agent.advance() is False and second["status"] == "queued"
    agent.on_text("120000")
    assert job["status"] == "ready" and agent.advance() is True


def test_a_question_left_unanswered_hands_the_job_back(env):
    clock = [1000.0]
    agent, tg, forms, job = make_agent(env, BASIC + [field("desired salary", "text", "Desired salary")])
    agent.now = lambda: clock[0]
    agent.advance()
    clock[0] += 5 * 3600
    agent.expire()
    assert job["status"] == "handed_off" and "No answer" in env.logged[0][1]["reason"]


def test_crash_while_submitting_is_not_retried(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    job["status"] = "submitting"
    agent.recover()
    assert job["status"] == "handed_off" and forms.submitted == 0
    assert "check your email" in env.logged[0][1]["reason"]


def test_state_survives_a_restart(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    again = apply_agent.Agent(FakeTelegram(), state_path=env.tmp / "state.json", forms=forms, log=env.log,
                              browser=fake_browser, profile=env.profile)
    assert again.jobs["j1"]["status"] == "ready" and again.jobs["j1"]["ver"] == 1


def test_queue_skips_unsupported_known_and_applied_jobs(env, monkeypatch):
    agent, *_ = make_agent(env, BASIC)
    env.entries["k"] = {"url": URL + "9"}
    agent.jobs.clear()
    cands = [{"url": URL, "title": "A", "fit": "high"}, {"url": "https://example.com/x", "title": "B", "fit": "high"},
             {"url": URL + "9", "title": "C", "fit": "high"}, {"url": URL + "5", "title": "D", "fit": "medium"}]
    added = agent.queue(cands, limit=5, fits=("high",))
    assert [j["title"] for j in added] == ["A"]
    assert agent.queue(cands, limit=5, fits=("high",)) == []


def test_lock_blocks_a_second_agent_until_stale(tmp_path):
    lock = tmp_path / "x.lock"
    assert apply_agent.acquire_lock(lock) is True
    assert apply_agent.acquire_lock(lock) is False
    import os
    old = lock.stat().st_mtime - 3600
    os.utime(lock, (old, old))
    assert apply_agent.acquire_lock(lock) is True


def test_listener_lock_is_marked_and_a_search_sees_it(tmp_path, monkeypatch):
    import daily_digest
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    lock = tmp_path / "apply_agent.lock"
    assert daily_digest._listener_running() is False
    assert apply_agent.acquire_lock(lock) is True  # an ordinary run is not a listener
    assert daily_digest._listener_running() is False
    lock.unlink()
    assert apply_agent.acquire_lock(lock, listener=True) is True
    assert lock.read_text().startswith("listen ")
    assert daily_digest._listener_running() is True
    import os
    old = lock.stat().st_mtime - 7200  # a listener that stopped touching its lock is not running
    os.utime(lock, (old, old))
    assert daily_digest._listener_running() is False


def test_search_leaves_a_request_for_a_running_listener(tmp_path, monkeypatch):
    import daily_digest
    import telegram_notify
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(telegram_notify, "read_env", lambda: {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
    started = []
    monkeypatch.setattr(daily_digest.subprocess, "run", lambda *a, **k: started.append(a))
    (tmp_path / "apply_agent.lock").write_text("listen 123")
    daily_digest._start_apply_agent()
    assert not started  # it did not start a second agent
    assert json.loads((tmp_path / "apply_queue_request.json").read_text())["day"] == date.today().isoformat()


def test_listener_queues_a_requested_day_once_and_keeps_going(env, monkeypatch):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.jobs.clear()
    request = env.tmp / "request.json"
    monkeypatch.setattr(apply_agent, "QUEUE_REQUEST_PATH", request)
    monkeypatch.setattr(apply_agent, "candidates", lambda day=None: [{"url": URL, "title": "A", "fit": "high"}])
    assert agent.take_queue_request() is False  # nothing asked for
    request.write_text(json.dumps({"day": "2026-10-28"}))
    assert agent.take_queue_request() is True
    assert not request.exists()  # taken, so the next loop does not queue again
    assert [j["title"] for j in agent.jobs.values()] == ["A"]
    assert agent.take_queue_request() is False


def test_listen_mode_serve_picks_up_a_request_between_polls(env, monkeypatch):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.jobs.clear()
    request = env.tmp / "request.json"
    monkeypatch.setattr(apply_agent, "QUEUE_REQUEST_PATH", request)
    monkeypatch.setattr(apply_agent, "candidates", lambda day=None: [{"url": URL, "title": "A", "fit": "high"}])
    request.write_text(json.dumps({"day": "2026-10-28"}))
    agent.serve(minutes=0.0001, wait=0, listen=True)
    assert [j["title"] for j in agent.jobs.values()] == ["A"]


def test_selftest_needs_the_tap():
    tg = FakeTelegram()
    tg.poll = lambda wait=0: [{"type": "button", "data": "t:selftest", "callback_id": "c"}]
    assert apply_agent.selftest(tg, wait=5) is True
    tg2 = FakeTelegram()
    assert apply_agent.selftest(tg2, wait=0) is False


def test_fit_jobs_on_other_sites_go_straight_to_recommended_with_one_message(env, monkeypatch):
    import applied_log
    monkeypatch.setattr(applied_log, "load_log", lambda path=None: {})
    agent, tg, *_ = make_agent(env, BASIC)
    agent.jobs.clear()
    cands = [{"url": URL, "title": "A", "company": "Acme", "fit": "medium"},
             {"url": "https://acme.wd1.myworkdayjobs.com/x", "title": "B", "company": "Acme", "fit": "high"},
             {"url": URL + "7", "title": "Low", "fit": "low"}]
    added = agent.queue(cands, hand_off_rest=True)
    assert [j["title"] for j in added] == ["A"]
    assert [(s, k["reason"][:11]) for s, k in env.logged] == [("recommended", "Applies on ")]
    assert len(tg.sent) == 1 and "nothing is sent until you tap Submit" in tg.sent[0][0]
    assert agent.queue(cands, hand_off_rest=True) == [] and len(env.logged) == 1  # never logged twice


def test_one_reminder_after_a_quiet_spell_and_the_deadline_keeps_running(env):
    clock = [1000.0]
    agent, tg, forms, job = make_agent(env, BASIC + [field("desired salary", "text", "Desired salary")])
    agent.now = lambda: clock[0]
    agent.advance()
    start, sent = job["updated"], len(tg.sent)
    clock[0] += 4 * 3600
    agent.expire()
    assert len(tg.sent) > sent and "Still waiting on you" in tg.sent[sent][0] and job["updated"] == start
    sent = len(tg.sent)
    clock[0] += 1800
    agent.expire()
    assert len(tg.sent) == sent  # only once
    clock[0] += 3600
    agent.expire()
    assert job["status"] == "handed_off"


# ---------------------------------------------------------------- not sent, reconcile, questions

def tap_data(tg, data):
    tg.events.append({"type": "button", "data": data, "callback_id": "cb"})
    for ev in tg.poll():
        tg.agent.handle(ev)


def text_in(agent, text):
    agent.handle({"type": "text", "text": text})


def ready_agent(env, clock):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.now = lambda: clock[0]
    tg.agent = agent
    agent.advance()
    assert job["status"] == "ready"
    return agent, tg, forms, job


def test_no_submit_tap_in_four_and_a_half_hours_logs_it_as_unsent(env):
    clock = [1000.0]
    agent, tg, forms, job = ready_agent(env, clock)
    clock[0] += 4 * 3600
    agent.expire()
    assert job["status"] == "ready"  # still waiting, only nudged
    clock[0] += 0.6 * 3600
    agent.expire()
    assert job["status"] == "unsent" and forms.submitted == 0
    status, kw = env.logged[-1]
    assert status == "unsent" and "no Submit tap" in kw["reason"] and "j1" in kw["reason"]
    assert "AI could have applied" in tg.sent[-1][0] and any(d.startswith("r:j1") for _, d in tg.last_buttons())


def test_an_unsent_job_can_still_be_submitted_from_its_old_button_or_unsent_command(env):
    clock = [1000.0]
    agent, tg, forms, job = ready_agent(env, clock)
    old = tg.last_buttons()[0][1]  # Submit button of the first summary
    clock[0] += 5 * 3600
    agent.expire()
    assert job["status"] == "unsent"
    text_in(agent, "/unsent")
    review = next(d for label, d in tg.last_buttons() if label.startswith("Review"))
    tap_data(tg, review)
    assert job["status"] == "ready" and forms.submitted == 0
    assert tg.last_buttons()[0][1] != old  # a fresh, versioned Submit button
    tap_data(tg, tg.last_buttons()[0][1])
    assert forms.submitted == 1 and job["status"] == "applied" and env.logged[-1][0] == "applied"


def test_old_submit_button_on_an_unsent_job_still_works(env):
    clock = [1000.0]
    agent, tg, forms, job = ready_agent(env, clock)
    submit = next(d for label, d in tg.last_buttons() if label == "Submit")
    clock[0] += 5 * 3600
    agent.expire()
    tap_data(tg, submit)
    assert forms.submitted == 1 and job["status"] == "applied"


def test_a_lump_sum_apply_elsewhere_moves_unsent_state_to_applied(env):
    clock = [1000.0]
    agent, tg, forms, job = ready_agent(env, clock)
    clock[0] += 5 * 3600
    agent.expire()
    env.entries["k"] = {"url": URL, "status": "applied", "applied_by": "AI"}
    assert agent.unsent_jobs() == []
    assert job["status"] == "applied" and job["note"] == "applied elsewhere"
    text_in(agent, "/unsent")
    assert "Nothing is waiting" in tg.sent[-1][0] and forms.submitted == 0


def test_a_free_text_question_spawns_an_agent_and_the_answer_goes_to_telegram(env):
    seen = {}

    def runner(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return SimpleNamespace(ok=True, text="You applied to 0 jobs today.", provider="claude", attempts=[])

    clock = [1000.0]
    agent, tg, forms, job = ready_agent(env, clock)
    agent.runner = runner
    text_in(agent, "How many jobs did I apply to today?")
    assert "You applied to 0 jobs today." in tg.sent[-1][0]
    assert "How many jobs did I apply to today?" in seen["prompt"] and seen["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert job["status"] == "ready" and forms.submitted == 0  # a question is not a draft edit or an answer


def test_a_reply_to_an_open_question_is_an_answer_even_without_a_question_mark(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("desired salary", "text", "Desired salary")])
    tg.agent = agent
    agent.runner = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn an agent"))
    agent.advance()
    assert job["status"] == "asking"
    text_in(agent, "120000")
    assert job["answers"]["desired salary"] == "120000"


def test_question_with_no_provider_says_so(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.runner = Runner(ok=False)
    text_in(agent, "/ask what is waiting?")
    assert "No AI provider answered" in tg.sent[-1][0]


def test_the_queue_message_separates_ai_jobs_from_hand_jobs(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.jobs.clear()
    cands = [{"title": "Eng A", "company": "Acme", "fit": "high", "url": URL},
             {"title": "Eng B", "company": "Beta", "fit": "medium", "url": "https://acme.wd1.myworkdayjobs.com/x/job/1"}]
    agent.queue(cands, hand_off_rest=True)
    message = tg.sent[-1][0]
    assert "AI is handling these 1" in message and "You apply to these 1 by hand" in message
    assert message.index("Eng A") < message.index("You apply to these") < message.index("Eng B")


def test_status_separates_the_buckets(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    env.entries["r"] = {"url": "x", "status": "recommended"}
    text = agent.status_text()
    assert "AI is handling" in text and "You apply by hand:</b> 1" in text


def test_a_queued_job_the_scorer_now_grades_low_is_skipped_without_preparing(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    env.fits[URL] = "low"
    assert agent.drop_regraded_low() == 1
    assert job["status"] == "skipped" and agent.advance() is False and forms.filled == [] and not tg.sent
    env.fits[URL] = "medium"  # a job still graded medium is kept
    job["status"] = "queued"
    assert agent.drop_regraded_low() == 0 and job["status"] == "queued"
