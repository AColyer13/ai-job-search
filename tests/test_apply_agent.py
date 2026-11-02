"""The apply agent's state machine with a fake Telegram, fake forms and no browser or model.

The rule under test: a known form is submitted with no tap and no question, and a robot check is not opened for anyone.
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

    def submit(self, page, **kw):
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


def make_agent(env, fields, runner=None, auto=False, **forms_kw):
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


def test_a_known_form_is_submitted_with_no_button(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert forms.filled[0]["first name"] == "Adam" and forms.filled[0]["resume cv"].endswith("resume.pdf")
    assert env.logged[0][0] == "applied" and env.logged[0][1]["resume"] == env.profile["resume_pdf"]
    assert tg.last_buttons() == []
    agent.serve(minutes=0.0001, wait=0)
    assert forms.submitted == 1


def test_an_old_submit_button_does_not_send_again(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    agent.on_button({"data": "s:j1:1", "callback_id": "cb"})
    assert forms.submitted == 1
    assert tg.answers == ["The agent applies on its own. Nothing is waiting on you."]


def test_a_question_the_profile_answers_is_not_asked(env):
    fields = BASIC + [field("are you authorized to work in the us", "radio", "Are you authorized to work in the US?",
                            options=["Yes", "No"])]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert job["status"] == "applied" and job["answers"]["are you authorized to work in the us"] == "Yes"
    assert tg.last_buttons() == []


def test_an_unknown_choice_is_answered_from_his_degree_and_saved(env):
    fields = BASIC + [field("highest honor", "select", "Highest honor", options=["Master's Degree", "Bachelor's Degree"])]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["highest honor"] == "Bachelor's Degree"
    assert apply_answers.load_bank(env.tmp / "bank.json")["highest honor"]["answer"] == "Bachelor's Degree"


def test_an_unmatched_choice_and_an_extra_file_still_send(env):
    fields = BASIC + [field("favorite color", "select", "Favorite color", options=["Red", "Blue"]),
                      field("portfolio", "file", "Portfolio")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner("A short draft."))
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["favorite color"] in ("Red", "Blue")
    assert job["answers"]["portfolio"] == env.profile["resume_pdf"]


def test_generated_wording_drops_ai_slop_and_does_not_invent_an_id_number():
    slop = "I want to leverage my sales work at Citizen Observer. I hope this helps!"
    cleaned = apply_answers.humanize_answer(slop)
    assert "leverage" not in cleaned.lower() and "hope this helps" not in cleaned.lower()
    assert "Citizen Observer" in cleaned and "use" in cleaned.lower()
    number, source = apply_answers.generate_answer(
        field("license", "text", "Driver's license number"), apply_answers.DEFAULT_PROFILE, {}, Runner())
    assert source == "generated" and not any(ch.isdigit() for ch in number)
    puzzle, _ = apply_answers.generate_answer(
        field("check", "text", "To help us prevent automated job applications, solve for X: 1 + X = 3"),
        apply_answers.DEFAULT_PROFILE, {}, Runner())
    assert puzzle == "2"


def test_an_unknown_question_is_answered_and_saved(env):
    fields = BASIC + [field("who referred you", "text", "Who referred you?")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner("Nobody referred me."))
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["who referred you"] == "Nobody referred me."
    assert apply_answers.load_bank(env.tmp / "bank.json")["who referred you"]["answer"] == "Nobody referred me."
    agent.on_text("120000")
    assert job["answers"]["who referred you"] == "Nobody referred me."


def test_cover_letter_file_is_drafted_rendered_and_attached(env):
    fields = BASIC + [field("cover letter", "file", "Cover Letter")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert forms.pdfs and job["answers"]["cover letter"].endswith("apply_j1.pdf")
    assert job["status"] == "applied"
    assert env.logged[0][1]["cover_letter"].endswith("apply_j1.pdf")
    assert any("A short draft." in t for t, _ in tg.sent)


def test_a_cover_letter_uses_his_own_letter_when_no_model_answers(env):
    fields = BASIC + [field("cover letter", "file", "Cover Letter")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner(ok=False))
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["cover letter"].endswith(".pdf") and forms.pdfs


def test_an_essay_the_model_cannot_answer_uses_the_base_letter(env):
    fields = BASIC + [field("why do you want to work here", "textarea", "Why do you want to work here?")]
    agent, tg, forms, job = make_agent(env, fields, runner=Runner(text="NEEDS_ADAM"))
    agent.advance()
    assert job["status"] == "applied" and "Citizen Observer" in job["answers"]["why do you want to work here"]


@pytest.mark.parametrize("blocker", ["sign in required", "email verification code"])
def test_blocker_hands_off_to_recommended(env, blocker):
    agent, tg, forms, job = make_agent(env, BASIC, blocker=blocker)
    agent.advance()
    assert job["status"] == "handed_off" and env.logged[0][0] == "attempted" and forms.submitted == 0


def test_a_robot_check_is_logged_and_chrome_is_not_opened(env):
    agent, tg, forms, job = make_agent(env, BASIC, blocker="CAPTCHA")
    agent.advance()
    assert job["status"] == "handed_off" and forms.submitted == 0 and not env.logged or env.logged[0][0] == "attempted"
    assert env.logged[0][0] == "attempted" and "robot check" in env.logged[0][1]["reason"].lower()
    assert "Chrome" not in tg.sent[-1][0]
    assert not hasattr(apply_agent, "run_assist") and not hasattr(agent, "start_assist")


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


def test_required_checkbox_consent_is_ticked_when_the_form_is_sent(env):
    fields = BASIC + [field("i agree to the privacy policy", "checkbox", "I agree to the privacy policy")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    assert job["answers"]["i agree to the privacy policy"] is True
    assert forms.submitted == 1 and forms.filled[0]["i agree to the privacy policy"] is True


def test_a_new_required_question_at_submit_is_answered_and_sent(env):
    agent, tg, forms, job = make_agent(env, BASIC, runner=Runner("Blue."))
    calls = {"n": 0}
    real = forms.discover

    def discover(page, url):
        calls["n"] += 1
        if calls["n"] > 1:
            forms.fields = BASIC + [field("favorite color", "text", "What is your favorite color?")]
        return real(page, url)

    forms.discover = discover
    agent.advance()
    assert forms.submitted == 1 and job["status"] == "applied"
    assert job["answers"]["favorite color"] == "Blue."
    assert apply_answers.load_bank(env.tmp / "bank.json")["favorite color"]["answer"] == "Blue."


def test_required_field_that_fails_to_fill_is_not_submitted(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    forms.fill_errors = {"email": "could not type"}
    agent.advance()
    assert forms.submitted == 0 and env.logged[0][0] == "attempted"


def test_dry_run_fills_but_never_submits_or_logs(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.dry_run = True
    agent.advance()
    assert forms.filled and forms.submitted == 0 and job["status"] == "dry_run" and not env.logged


@pytest.mark.parametrize("result,status", [(("unknown", "no confirmation"), "handed_off"),
                                           (("invalid", "Email is required"), "handed_off"),
                                           (("blocked", "email verification code"), "handed_off")])
def test_unconfirmed_submit_is_never_logged_as_applied(env, monkeypatch, result, status):
    monkeypatch.setattr(apply_agent.mail_codes, "find_confirmation", lambda *a, **k: False)
    agent, tg, forms, job = make_agent(env, BASIC, submit_result=result)
    agent.advance()
    if result[0] == "unknown":
        assert forms.submitted == 2
        assert job["status"] == "handed_off"
        assert env.logged[0][0] == "attempted"
        assert "no confirmation email" in job["reason"] and "second attempt" in job["reason"]
        assert apply_agent.daily_digest.not_applied_kind(job["reason"]) == "other"
        assert job["submission_unconfirmed"] is True
    else:
        assert job["status"] == status and [s for s, _ in env.logged] == ["attempted"]


def test_a_confirmation_email_logs_the_job_applied_when_the_page_stays_quiet(env, monkeypatch):
    monkeypatch.setattr(apply_agent.mail_codes, "find_confirmation", lambda *a, **k: True)
    agent, tg, forms, job = make_agent(env, BASIC, submit_result=("unknown", "no confirmation"))
    agent.advance()
    assert job["status"] == "applied" and env.logged[0][0] == "applied"


def test_uncertain_submission_cannot_be_queued_under_another_url_or_location(env, monkeypatch):
    monkeypatch.setattr(apply_agent.mail_codes, "find_confirmation", lambda *a, **kw: False)
    agent, _, _, job = make_agent(env, BASIC, auto=True, submit_result=("unknown", "connection lost"))
    agent.advance()
    assert job["submission_unconfirmed"] is True
    assert agent.queue([{**job, "url": "https://another-board.example/role", "location": "Remote"}]) == []


def test_uncertain_log_reason_blocks_duplicates_even_without_agent_history(env):
    env.entries["old"] = {"company": "Acme", "title": "Engineer", "location": "London",
                          "url": "https://old.example/123", "status": "recommended",
                          "reason": "Submission unconfirmed; no receipt found."}
    agent, _, _, _ = make_agent(env, BASIC)
    agent.jobs.clear()
    assert agent.queue([{"company": "Acme", "title": "Engineer", "location": "Remote", "fit": "high",
                         "url": "https://new.example/123"}]) == []


def test_applied_role_blocks_another_url_with_different_location_metadata(env):
    env.entries["old"] = {"company": "Acme", "title": "Engineer", "location": "United States",
                          "url": "https://employer.example/123", "status": "applied"}
    agent, _, _, _ = make_agent(env, BASIC)
    agent.jobs.clear()
    assert agent.queue([{"company": "Acme", "title": "Engineer", "location": "New York, United States",
                         "work_mode": "remote", "fit": "high", "url": "https://aggregator.example/123"}]) == []


def test_applied_role_does_not_block_a_different_role_at_same_employer(env):
    env.entries["old"] = {"company": "Acme", "title": "Engineer", "location": "United States",
                          "url": "https://employer.example/123", "status": "applied"}
    agent, _, _, _ = make_agent(env, BASIC)
    agent.jobs.clear()
    assert len(agent.queue([{"company": "Acme", "title": "Solutions Consultant", "location": "Remote",
                             "fit": "high", "url": "https://employer.example/456"}])) == 1


def test_there_is_no_skip_button_and_no_ill_do_it_button(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    assert job["status"] == "applied" and tg.last_buttons() == []
    agent.on_text("/skip")
    assert job["status"] == "applied"


def test_confirmation_email_overrides_a_failed_verification_ui(env, monkeypatch):
    agent, _, _, job = make_agent(env, BASIC)
    monkeypatch.setattr(apply_agent.mail_codes, 'find_confirmation', lambda *args, **kwargs: True)
    agent.finish_submit(job, 'blocked', 'email verification code: the site did not accept the code')
    assert job['status'] == 'applied'
    assert env.logged[-1][0] == 'applied'


def test_submit_exception_quarantines_instead_of_allowing_requeue(env):
    agent, _, forms, job = make_agent(env, BASIC)
    agent.prepare(job)
    def broken(*args, **kwargs):
        raise ConnectionError('connection lost after click')
    forms.submit = broken
    agent.submit_job(job)
    assert job['submission_unconfirmed'] is True
    assert agent.queue([{**job, 'url': 'https://another.example/job'}]) == []


def test_many_questions_prepare_when_asking_is_off_and_gpa_still_stops(env):
    extra = [field("car", "radio", "Do you own a car?", options=["Yes", "No"]),
             field("estonian", "select", "What is your Estonian proficiency?", options=["None", "Fluent"]),
             field("county", "text", "County"),
             field("kafka", "radio", "Have you sold Kafka?", options=["Yes", "No"])]
    agent, tg, forms, job = make_agent(env, BASIC + extra, auto=True)
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["car"] == "Yes" and job["answers"]["estonian"] == "None"
    assert job["answers"]["county"] == "Hennepin" and job["answers"]["kafka"] == "No"
    assert tg.last_buttons() == []
    gpa_agent, _, gpa_forms, gpa = make_agent(env, BASIC + [field("gpa", "text", "What is your GPA?")], auto=True)
    gpa_agent.ask_adam = False
    gpa_agent.advance()
    assert gpa["status"] == "handed_off" and "GPA" in gpa["reason"] and gpa_forms.submitted == 0


def test_a_rejected_email_code_is_tried_once_more(env, monkeypatch):
    monkeypatch.setattr(apply_agent.mail_codes, "find_confirmation", lambda *a, **k: False)
    agent, _, forms, job = make_agent(env, BASIC, auto=True, submit_result=("blocked", "the site did not accept the code"))
    agent.ask_adam = False
    agent.advance()
    assert forms.submitted == 2 and job["status"] == "handed_off"
    assert "second attempt" in job["reason"]
    assert apply_agent.daily_digest.not_applied_kind(job["reason"]) == "other"


def test_autonomous_robot_check_does_not_open_a_manual_helper(env):
    agent, _, forms, job = make_agent(env, BASIC, auto=True, blocker="CAPTCHA")
    agent.advance()
    assert job["status"] == "handed_off" and forms.submitted == 0
    assert "robot check" in job["reason"].lower()


def test_a_finished_job_does_not_hold_up_the_next_one(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    second = dict(job, id="j2", url=URL + "4", status="queued", answers={}, fields={}, drafts={}, pending=[])
    agent.jobs["j2"] = second
    assert agent.advance() is True and job["status"] == "applied" and second["status"] == "queued"
    assert agent.advance() is True and second["status"] == "applied"
    assert forms.submitted == 2 and tg.last_buttons() == []


def test_an_unanswered_question_does_not_stall_the_queue(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("who referred you", "text", "Who referred you?")])
    second = dict(job, id="j2", url=URL + "4", status="queued", answers={}, fields={}, drafts={}, pending=[])
    agent.jobs["j2"] = second
    assert agent.advance() is True and job["status"] == "applied"
    assert agent.advance() is True and second["status"] == "applied"
    assert forms.submitted == 2


def test_an_unknown_question_is_sent_without_waiting(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("who referred you", "text", "Who referred you?")])
    agent.advance()
    assert job["status"] == "applied" and env.logged[0][0] == "applied"
    assert forms.submitted == 1 and "who referred you" in job["answers"]


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
    assert again.jobs["j1"]["status"] == "applied" and again.jobs["j1"]["ver"] == 1


def test_queue_tries_every_fit_job_whatever_the_link_and_skips_known_and_applied(env, monkeypatch):
    agent, *_ = make_agent(env, BASIC)
    env.entries["k"] = {"url": URL + "9"}
    agent.jobs.clear()
    cands = [{"url": URL, "title": "A", "fit": "high"}, {"url": "https://example.com/x", "title": "B", "fit": "high"},
             {"url": URL + "9", "title": "C", "fit": "high"}, {"url": URL + "5", "title": "D", "fit": "medium"}]
    added = agent.queue(cands, limit=5, fits=("high",))
    assert [j["title"] for j in added] == ["A", "B"]  # an unfamiliar site is tried, not assumed unfillable
    assert agent.queue(cands, limit=5, fits=("high",)) == []


def test_lock_blocks_a_second_agent_even_when_heartbeat_is_stale(tmp_path):
    lock = tmp_path / "x.lock"
    assert apply_agent.acquire_lock(lock) is True
    assert apply_agent.acquire_lock(lock) is False
    import os
    old = lock.stat().st_mtime - 3600
    os.utime(lock, (old, old))
    assert apply_agent.acquire_lock(lock) is False
    apply_agent.release_lock(lock)
    assert apply_agent.acquire_lock(lock) is True
    apply_agent.release_lock(lock)


def test_listener_lock_is_marked_and_a_search_sees_it(tmp_path, monkeypatch):
    import daily_digest
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    lock = tmp_path / "apply_agent.lock"
    assert daily_digest._listener_running() is False
    assert apply_agent.acquire_lock(lock) is True  # an ordinary run is not a listener
    assert daily_digest._listener_running() is False
    apply_agent.release_lock(lock)
    assert apply_agent.acquire_lock(lock, listener=True) is True
    assert lock.read_text().startswith("listen ")
    assert daily_digest._listener_running() is True
    import os
    old = lock.stat().st_mtime - 7200  # a long browser call must not make a live listener disappear
    os.utime(lock, (old, old))
    assert daily_digest._listener_running() is True
    apply_agent.release_lock(lock)


def test_search_leaves_a_request_for_a_running_listener(tmp_path, monkeypatch):
    import daily_digest
    import telegram_notify
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(telegram_notify, "read_env", lambda: {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
    started = []
    monkeypatch.setattr(daily_digest.subprocess, "run", lambda *a, **k: started.append(a))
    import os
    (tmp_path / "apply_agent.lock").write_text(f"listen {os.getpid()}")
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


def _fld(key, kind="textarea", label=None, required=True):
    return apply_forms.Field(kind=kind, label=label or key, key=key, required=required, options=[])


def test_standard_answers_come_from_the_facts_file_and_fill_matching_questions():
    standard = apply_answers.standard_answers()
    assert len(standard) >= 4
    fld = _fld("whats something you want to share about yourself that isnt obvious from your resume",
               label="What's something you want to share about yourself that isn't obvious from your resume?")
    outcome, value, source = apply_answers.resolve(fld, apply_answers.DEFAULT_PROFILE, {}, standard)
    assert (outcome, source) == ("fill", "standard") and "VR headset" in value
    # a close rewording still matches; a different question does not
    reworded = _fld("why are you passionate about testing and qa", label="Why are you passionate about testing and QA?")
    assert apply_answers.resolve(reworded, apply_answers.DEFAULT_PROFILE, {}, standard)[2] == "standard"
    other = _fld("why do you want to work here", label="Why do you want to work here?")
    assert apply_answers.resolve(other, apply_answers.DEFAULT_PROFILE, {}, standard)[0] == "draft"


def test_answer_log_keeps_each_question_once_and_feeds_the_next_draft(tmp_path):
    log = tmp_path / "log.json"
    job = {"title": "QA Engineer", "company": "Acme"}
    assert apply_answers.log_answer("Why QA?", "Because quality.", "Adam", job, path=log) is True
    assert apply_answers.log_answer("why qa", "Because quality.", "Adam", job, path=log) is False  # same question and answer
    assert apply_answers.log_answer("Why QA?", "", "Adam", job, path=log) is False
    assert apply_answers.recent_answers(5, log)[0]["job"] == "QA Engineer | Acme"
    prompt = apply_answers.draft_prompt("Why us?", {"title": "T", "company": "C"}, "answer",
                                        history=apply_answers.recent_answers(5, log))
    assert "Q: Why QA?" in prompt and "Because quality." in prompt


def test_cover_letter_prompt_starts_from_adams_own_letter():
    prompt = apply_answers.draft_prompt("Write a cover letter", {"title": "T", "company": "C"}, "cover letter", history=[])
    assert "<base_cover_letter>" in prompt and "nearly five years in B2B software sales" in prompt
    assert "<base_cover_letter>" not in apply_answers.draft_prompt("Why us?", {"title": "T", "company": "C"}, "answer", history=[])


def test_the_sent_draft_is_logged_and_shown_by_answers_command(env):
    fields = BASIC + [field("why do you want to work here", "textarea", "Why do you want to work here?")]
    agent, tg, forms, job = make_agent(env, fields)
    agent.advance()
    log = apply_answers.recent_answers(10, env.tmp / "apply_answer_log.json")
    sources = {e["question"]: e["source"] for e in log}
    assert sources["Why do you want to work here?"].startswith("AI draft")
    agent.on_text("/answers")
    assert "Why do you want to work here?" in tg.sent[-1][0]


def test_selftest_needs_the_tap():
    tg = FakeTelegram()
    tg.poll = lambda wait=0: [{"type": "button", "data": "t:selftest", "callback_id": "c"}]
    assert apply_agent.selftest(tg, wait=5) is True
    tg2 = FakeTelegram()
    assert apply_agent.selftest(tg2, wait=0) is False


def test_fit_jobs_on_other_sites_are_queued_like_any_other_with_one_message(env, monkeypatch):
    import applied_log
    monkeypatch.setattr(applied_log, "load_log", lambda path=None: {})
    agent, tg, *_ = make_agent(env, BASIC)
    agent.jobs.clear()
    cands = [{"url": URL, "title": "A", "company": "Acme", "fit": "medium"},
             {"url": "https://acme.wd1.myworkdayjobs.com/x", "title": "B", "company": "Acme", "fit": "high"},
             {"url": URL + "7", "title": "Low", "fit": "low"}]
    added = agent.queue(cands)
    assert [j["title"] for j in added] == ["A", "B"] and not env.logged  # nothing is logged until a page has been tried
    assert len(tg.sent) == 1 and "trying 2 applications" in tg.sent[0][0]
    assert agent.queue(cands) == []  # never queued twice


def test_an_unknown_question_is_not_nudged(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("who referred you", "text", "Who referred you?")])
    agent.advance()
    assert job["status"] == "applied"
    assert not any("Still waiting" in text for text, _ in tg.sent)


# ---------------------------------------------------------------- not sent, reconcile, questions

def tap_data(tg, data):
    tg.events.append({"type": "button", "data": data, "callback_id": "cb"})
    for ev in tg.poll():
        tg.agent.handle(ev)


def text_in(agent, text):
    agent.handle({"type": "text", "text": text})


def test_a_known_job_is_not_left_unsent(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    tg.agent = agent
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert not any(status == "unsent" for status, _ in env.logged)
    text_in(agent, "/unsent")
    assert "Nothing is waiting" in tg.sent[-1][0]


def test_a_lump_sum_apply_elsewhere_marks_the_state_applied(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    tg.agent = agent
    env.entries["k"] = {"url": URL, "status": "applied", "applied_by": "AI"}
    assert agent.reconcile() == 1
    assert job["status"] == "applied" and job["note"] == "applied elsewhere"
    assert forms.submitted == 0


def test_a_free_text_question_spawns_an_agent_and_the_answer_goes_to_telegram(env):
    seen = {}

    def runner(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return SimpleNamespace(ok=True, text="You applied to 0 jobs today.", provider="claude", attempts=[])

    agent, tg, forms, job = make_agent(env, BASIC)
    tg.agent = agent
    agent.runner = runner
    text_in(agent, "How many jobs did I apply to today?")
    assert "You applied to 0 jobs today." in tg.sent[-1][0]
    assert "How many jobs did I apply to today?" in seen["prompt"] and seen["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert job["status"] == "queued" and forms.submitted == 0


def test_a_typed_reply_does_not_fill_a_skipped_question(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("who referred you", "text", "Who referred you?")])
    tg.agent = agent
    agent.runner = Runner("Noted.")
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    text_in(agent, "120000")
    assert job["answers"]["who referred you"] != "120000"


def test_question_with_no_provider_says_so(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.runner = Runner(ok=False)
    text_in(agent, "/ask what is waiting?")
    assert "No AI provider answered" in tg.sent[-1][0]


def test_the_queue_message_lists_every_job_and_promises_no_hand_off_by_link(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.jobs.clear()
    cands = [{"title": "Eng A", "company": "Acme", "fit": "high", "url": URL},
             {"title": "Eng B", "company": "Beta", "fit": "medium", "url": "https://acme.wd1.myworkdayjobs.com/x/job/1"}]
    agent.queue(cands)
    message = tg.sent[-1][0]
    assert "I'm trying 2 applications" in message and "apply by hand" not in message
    assert message.index("Eng A") < message.index("Eng B")


def test_status_separates_the_buckets(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    env.entries["r"] = {"url": "x", "status": "recommended", "reason": "Adam chose I'll do it"}
    text = agent.status_text()
    assert "AI is handling" in text and "Not applied:</b> 1" in text


def test_a_queued_job_the_scorer_now_grades_low_is_skipped_without_preparing(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    env.fits[URL] = "low"
    assert agent.drop_regraded_low() == 1
    assert job["status"] == "skipped" and agent.advance() is False and forms.filled == [] and not tg.sent
    env.fits[URL] = "medium"  # a job still graded medium is kept
    job["status"] = "queued"
    assert agent.drop_regraded_low() == 0 and job["status"] == "queued"


def test_state_and_country_come_from_the_profile_and_blank_address_is_asked():
    profile = {**apply_answers.DEFAULT_PROFILE, "address": "", "zip": ""}
    def field(label, kind="text", options=()):
        return apply_forms.Field(kind=kind, label=label, key=apply_forms.normalize_label(label), required=True,
                                 options=list(options), selectors=["x"])
    assert apply_answers.resolve(field("State"), profile, {})[:2] == ("fill", "Minnesota")
    assert apply_answers.resolve(field("Country"), profile, {})[:2] == ("fill", "United States")
    assert apply_answers.resolve(field("Street Address"), profile, {})[0] == "ask"  # no address set: never guessed
    profile["address"] = "1 Main St"
    assert apply_answers.resolve(field("Street Address"), profile, {})[:2] == ("fill", "1 Main St")


def test_option_aliases_match_usa_and_mn_but_not_inside_other_words():
    assert apply_forms.best_option(["Canada", "USA"], "United States") == "USA"
    assert apply_forms.best_option(["Australia", "Canada"], "United States") is None
    assert apply_forms.best_option(["MN", "WI"], "Minnesota") == "MN"


@pytest.mark.parametrize("label", ["Preferred First Name*", "Preferred name", "Nickname", "What should we call you?"])
def test_a_preferred_name_is_adams_first_name_not_a_question(label):
    f = apply_forms.Field(kind="text", label=label, key=apply_forms.normalize_label(label), required=True, options=[], selectors=["x"])
    assert apply_answers.resolve(f, apply_answers.DEFAULT_PROFILE, {})[:2] == ("fill", "Adam")


# ---- auto-submit: a human is needed only for a question that was never answered

def test_auto_submit_sends_without_a_tap_and_says_what_it_sent(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=True)
    agent.advance()
    assert forms.submitted == 1 and job["status"] == "applied" and not job.get("asked_adam")
    assert env.logged[0][0] == "applied"
    assert "Applied" in tg.sent[-1][0] and "Resume" in tg.sent[-1][0]
    assert not any("Ready" in t for t, _ in tg.sent)  # no summary waits for a tap


def test_an_unknown_question_is_saved_and_not_asked(env):
    fields = BASIC + [field("favourite colour")]
    agent, tg, forms, job = make_agent(env, fields, auto=True, runner=Runner("Blue."))
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1
    assert job["answers"]["favourite colour"] == "Blue."
    assert apply_answers.load_bank(env.tmp / "bank.json")["favourite colour"]["answer"] == "Blue."
    assert tg.last_buttons() == []


def test_there_is_no_submit_tap_to_turn_back_on(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=False)
    agent.advance()
    assert job["status"] == "applied" and forms.submitted == 1 and tg.last_buttons() == []


def test_a_job_left_waiting_for_a_tap_is_sent_when_the_agent_restarts(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=False)
    job["status"] = "ready"
    job["answers"] = {"first name": "Adam", "last name": "Colyer", "email": "adamcolyer@gmail.com",
                      "resume cv": env.profile["resume_pdf"]}
    job["fields"] = {key: {"label": key, "kind": "text"} for key in job["answers"]}
    agent.recover()
    assert forms.submitted == 1 and job["status"] == "applied"


def test_a_robot_check_is_not_sent_and_nobody_is_asked_to_finish_it(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=True, blocker="CAPTCHA")
    agent.advance()
    assert job["status"] == "handed_off" and forms.submitted == 0
    assert [s for s, _ in env.logged] == ["attempted"]
    assert "robot check" in env.logged[0][1]["reason"].lower()
    assert not any("Your turn" in t or "Over to you" in t or "Chrome" in t for t, _ in tg.sent)


def test_the_agent_has_no_chrome_helper_to_launch(tmp_path):
    assert not hasattr(apply_agent, "run_assist") and not hasattr(apply_agent, "launch_assist")


def test_a_failed_log_write_is_kept_and_retried(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    calls = []

    def flaky(j, status, **kw):
        calls.append(status)
        return len(calls) > 1  # the first write fails (Word has the file open), the retry works

    agent.log_write = flaky
    assert agent.log(job, "applied", note="x") is False
    assert job["unlogged"] == {"status": "applied", "kw": {"note": "x"}}
    assert agent.retry_unlogged() == 1
    assert "unlogged" not in job and calls == ["applied", "applied"]


def test_a_log_write_that_raises_is_kept_not_lost(env):
    agent, tg, forms, job = make_agent(env, BASIC)

    def boom(j, status, **kw):
        raise OSError("locked")

    agent.log_write = boom
    assert agent.log(job, "recommended", reason="r") is False and job["unlogged"]["status"] == "recommended"


def test_audit_command_reports_a_problem_and_then_all_clear(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    job["status"] = "applied"
    agent.audit_rows = lambda: []
    agent.on_text("/audit")
    assert "SENT BUT NOT LOGGED" in tg.sent[-1][0]
    env.entries["k"] = {"url": URL, "title": "Engineer", "company": "Acme", "status": "applied", "applied_by": "AI", "resume": "r.pdf"}
    agent.audit_rows = lambda: [{"company": "Acme", "role": "Engineer", "source": URL}]
    agent.on_text("/audit")
    assert "agree" in tg.sent[-1][0]


def test_the_listener_alerts_once_about_a_lost_record(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    job["status"] = "applied"
    agent.audit_rows = lambda: []
    agent.check_audit()
    agent.check_audit()
    alerts = [t for t, _ in tg.sent if "Tracking problem" in t]
    assert len(alerts) == 1



def _errors(agent):
    return [json.loads(line) for line in agent.errors_path.read_text(encoding="utf-8").splitlines()]


def test_record_error_keeps_where_type_origin_and_job(tmp_path):
    path = tmp_path / "errs.jsonl"
    try:
        {}["missing"]
    except KeyError as e:
        assert apply_agent.record_error("submit", e, {"company": "Acme", "title": "Eng", "ats": "lever"}, path=path, now=1000)
    (row,) = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (row["where"], row["type"], row["company"], row["ats"]) == ("submit", "KeyError", "Acme", "lever")
    assert "test_apply_agent.py" in row["origin"] and "test_record_error" in row["origin"] and row["repeats"] == 0


def test_a_repeating_error_is_counted_not_flooded(tmp_path):
    path = tmp_path / "errs.jsonl"
    err = TimeoutError("getUpdates")
    assert apply_agent.record_error("telegram_poll", err, path=path, now=0)
    assert not apply_agent.record_error("telegram_poll", err, path=path, now=5)
    assert not apply_agent.record_error("telegram_poll", err, path=path, now=10)
    assert apply_agent.record_error("telegram_poll", err, path=path, now=apply_agent.ERROR_REPEAT_SECONDS + 1)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["repeats"] for r in rows] == [0, 2]
    # a different error is its own row straight away
    assert apply_agent.record_error("telegram_poll", ValueError("x"), path=path, now=11)


def test_a_failed_log_write_lands_in_the_errors_log(env):
    agent, tg, forms, job = make_agent(env, BASIC)

    def boom(j, status, **kw):
        raise OSError("locked")

    agent.log_write = boom
    agent.log(job, "applied")
    (row,) = _errors(agent)
    assert row["where"] == "applied_log" and row["type"] == "OSError" and row["company"] == "Acme"


def test_a_submit_that_raises_is_logged_with_its_job(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    forms.discover = lambda page, url: (_ for _ in ()).throw(RuntimeError("page crashed"))
    job["answers"] = {"first name": "Adam"}
    agent.submit_job(job)
    assert job["status"] == "handed_off"
    assert [(r["where"], r["type"], r["message"]) for r in _errors(agent)] == [("submit", "RuntimeError", "page crashed")]


def test_a_form_error_the_agent_expects_is_not_an_error(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    forms.discover = lambda page, url: (_ for _ in ()).throw(apply_forms.FormError("the posting is closed or gone"))
    agent.advance()
    assert not agent.errors_path.exists()


def test_errors_command_summarises_by_count(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.on_text("/errors")
    assert "No errors logged" in tg.sent[-1][0]
    for _ in range(2):
        agent.note_error("submit", RuntimeError("boom"), job)
    agent.note_error("telegram_poll", TimeoutError("t"))
    agent.on_text("/errors")
    text = tg.sent[-1][0]
    assert "submit" in text and "RuntimeError" in text and "telegram_poll" in text


def test_a_submit_crash_is_still_logged_on_the_job(env):
    agent, tg, forms, job = make_agent(env, BASIC)

    def broken(page, **kw):
        raise RuntimeError("chrome would not start")

    forms.submit = broken
    agent.advance()
    assert job["status"] == "handed_off"
    row = _errors(agent)[0]
    assert row["where"] == "submit" and row["message"] == "chrome would not start"


def test_a_site_that_stops_the_script_logs_attempted_and_pulls_nobody_in(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=True)
    forms.submit = lambda page, **kw: ("blocked", "email verification code: no code arrived")
    agent.advance()
    assert job["status"] == "handed_off" and [s for s, _ in env.logged] == ["attempted"]
    assert "no code arrived" in env.logged[0][1]["reason"]
    sent = " ".join(t for t, _ in tg.sent)
    assert "Could not finish" in sent and "Over to you" not in sent and "Your turn" not in sent
    assert any(r["kind"] == "attempted" for r in map(json.loads, agent.issues_path.read_text().splitlines()))


def test_the_submit_gets_the_email_code_reader(env):
    agent, tg, forms, job = make_agent(env, BASIC, auto=True)
    seen = {}
    forms.submit = lambda page, **kw: (seen.update(kw), ("submitted", ""))[1]
    agent.advance()
    assert job["status"] == "applied" and callable(seen["code_getter"])


def test_apply_settings_come_from_env_before_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APPLY_AUTO_SUBMIT=0\n")
    monkeypatch.setattr(apply_agent.telegram_notify, "ENV_PATH", env_file)
    monkeypatch.setenv("APPLY_AUTO_SUBMIT", "1")
    assert apply_agent.setting("APPLY_AUTO_SUBMIT", "1") == "0"
    assert apply_agent.setting("APPLY_NOT_SET", "x") == "x"


def test_a_lock_left_by_a_dead_process_does_not_block_the_restart(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("listen 999999")  # no such process
    assert apply_agent.acquire_lock(lock, listener=True) is True
    assert apply_agent.acquire_lock(lock, listener=True) is False  # now held by this live process


def test_a_required_gpa_moves_on_from_the_job_without_asking_anyone(env):
    agent, tg, forms, job = make_agent(env, BASIC + [field("gpa", "text", "GPA")])
    agent.advance()
    assert job["status"] == "handed_off" and env.logged[0][0] == "attempted" and "GPA" in env.logged[0][1]["reason"]
    assert forms.submitted == 0 and not any("Question" in t for t, _ in tg.sent)


def test_jobs_handed_back_only_for_their_address_are_tried_again(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    old = "Applies on himalayas.app, which the agent cannot fill (it knows Greenhouse, Lever and Ashby)."
    job.update(status="handed_off", reason=old)
    kept = dict(job, id="j2", url=URL + "9", status="handed_off", reason="The posting is closed or gone.")
    agent.jobs["j2"] = kept
    assert agent.reopen_unfilled() == 1
    assert job["status"] == "queued" and job["reason"] == "" and kept["status"] == "handed_off"


def test_queue_retries_an_old_cannot_fill_recommendation_but_not_one_adam_chose(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.jobs.clear()
    stale = "https://himalayas.app/companies/a/jobs/one"
    chosen = "https://himalayas.app/companies/b/jobs/two"
    env.entries.update({
        "a": {"url": stale, "status": "recommended", "reason": "Applies on himalayas.app, which the agent cannot fill (it knows Greenhouse)."},
        "b": {"url": chosen, "status": "recommended", "reason": "I'll do it myself."}})
    cands = [{"url": stale, "title": "A", "company": "A", "fit": "high"}, {"url": chosen, "title": "B", "company": "B", "fit": "high"}]
    assert [j["title"] for j in agent.queue(cands)] == ["A"]


def test_the_listener_keeps_applying_outside_the_old_daytime_window(env, monkeypatch):
    from datetime import datetime
    agent, tg, forms, job = make_agent(env, BASIC)
    evening = datetime(2026, 10, 29, 17).timestamp()
    agent.now = lambda: evening
    polls = []

    def poll(timeout=0):
        polls.append(evening)
        raise SystemExit

    tg.poll = poll
    monkeypatch.setattr(apply_agent.time, "sleep", lambda seconds: None)
    with pytest.raises(SystemExit):
        agent.serve(listen=True, wait=0)
    assert polls == [evening] and job["status"] == "applied" and forms.submitted == 1


def test_a_reason_nothing_classifies_is_written_to_the_errors_log(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.handoff(job, "Only via Handshake with school alumni access.")
    assert [(r["where"], r["job"]["id"] if isinstance(r.get("job"), dict) else r.get("job")) for r in _errors(agent)][0][0] == "not_applied_other"
    assert "Handshake" in _errors(agent)[0]["message"]


def test_a_known_reason_is_not_an_error(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.handoff(job, "This posting is closed or gone", attempted=False)
    assert not agent.errors_path.exists() or not _errors(agent)
