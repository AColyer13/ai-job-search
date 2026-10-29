"""Nothing is sent to an employer unless Adam tapped Submit on the summary that shows the answers about to be sent.

Every case drives the Telegram button handler of a Greenhouse job, with a fake form that counts submit clicks.
"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import apply_agent  # noqa: E402

from tests.test_apply_agent import BASIC, env, field, make_agent  # noqa: E402,F401  (env is a fixture)


def ready(env, fields=None, **kw):
    """An agent holding job j1 with every answer filled and its approval summary sent."""
    agent, tg, forms, job = make_agent(env, fields or BASIC, **kw)
    job["answers"] = {"first name": "Adam", "last name": "Colyer", "email": "adamcolyer@gmail.com",
                      "resume cv": env.profile["resume_pdf"]}
    job["fields"] = {k: {"label": k, "kind": "text", "options": []} for k in job["answers"]}
    agent.approval(job)
    return agent, tg, forms, job


def tap(agent, job, ver=None, data=None):
    ver = job["ver"] if ver is None else ver
    agent.on_button({"data": data or f"s:{job['id']}:{ver}", "callback_id": "cb"})


def test_the_matching_summary_submits_exactly_once(env):
    agent, tg, forms, job = ready(env)
    tap(agent, job)
    assert forms.submitted == 1 and job["status"] == "applied"
    tap(agent, job)  # a second tap on the same button
    assert forms.submitted == 1


def test_an_older_summary_cannot_submit(env):
    agent, tg, forms, job = ready(env)
    old = job["ver"]
    agent.approval(job)  # a newer summary replaces it
    tap(agent, job, ver=old)
    assert forms.submitted == 0 and job["status"] == "ready"


@pytest.mark.parametrize("data", ["s:j1", "s:j1:", "s:j1:abc", "s:nope:1", "s"])
def test_a_submit_button_without_a_valid_version_does_nothing(env, data):
    agent, tg, forms, job = ready(env)
    tap(agent, job, data=data)
    assert forms.submitted == 0 and job["status"] == "ready"


@pytest.mark.parametrize("status", ["queued", "preparing", "asking", "submitting", "handed_off", "skipped", "applied", "dry_run"])
def test_only_a_ready_or_unsent_job_can_be_submitted(env, status):
    agent, tg, forms, job = ready(env)
    job["status"] = status
    tap(agent, job)
    assert forms.submitted == 0


def test_replacing_a_draft_invalidates_the_old_button(env):
    agent, tg, forms, job = ready(env)
    job["drafts"] = {"cover letter": {"label": "Cover letter", "text": "first", "provider": "claude", "letter": False}}
    job["answers"]["cover letter"] = "first"
    agent.approval(job)
    old = job["ver"]
    agent.replace_draft(job, "second")
    assert job["ver"] != old and job["answers"]["cover letter"] == "second"
    tap(agent, job, ver=old)
    assert forms.submitted == 0
    tap(agent, job)
    assert forms.submitted == 1


def test_answers_changed_after_the_summary_cannot_be_submitted_with_its_button(env):
    agent, tg, forms, job = ready(env)
    shown = copy.deepcopy(job["answers"])
    job["answers"]["email"] = "someone.else@example.com"  # state changed without a new summary
    tap(agent, job)
    assert forms.submitted == 0
    assert forms.filled == [] and shown != job["answers"]


def test_the_same_answers_still_submit_after_a_state_reload(env):
    agent, tg, forms, job = ready(env)
    agent.save()
    again = apply_agent.Agent(agent.tg, state_path=agent.state_path, forms=forms, log=env.log, browser=agent.browser_factory,
                              profile=env.profile, bank_path=env.tmp / "bank.json", letter_dir=env.tmp / "letters",
                              shot_dir=env.tmp / "shots", log_entries=lambda: env.entries, fit_of=lambda url: None)
    tap(again, again.jobs["j1"])
    assert forms.submitted == 1


def test_a_dry_run_never_clicks_submit(env):
    agent, tg, forms, job = ready(env)
    agent.dry_run = True
    tap(agent, job)
    assert forms.submitted == 0 and job["status"] == "dry_run"


def test_a_form_that_gained_a_required_question_is_handed_back_unsubmitted(env):
    agent, tg, forms, job = ready(env)
    forms.fields = list(BASIC) + [field("why do you want this job")]
    tap(agent, job)
    assert forms.submitted == 0 and forms.filled == [] and job["status"] == "handed_off"


def test_a_page_that_now_shows_a_captcha_is_never_submitted_by_the_script(env):
    agent, tg, forms, job = ready(env, blocker="CAPTCHA")
    tap(agent, job)
    # the script does not click through a robot check: the filled form goes to Adam's Chrome window instead
    assert forms.submitted == 0 and job["status"] == "assisting" and not env.logged
