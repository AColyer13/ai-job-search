"""A known application is sent without a Submit button. An old button does not send anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))

from tests.test_apply_agent import BASIC, env, make_agent  # noqa: E402,F401


def test_a_known_form_is_sent_without_a_button(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    assert forms.submitted == 1 and job["status"] == "applied"
    assert tg.last_buttons() == []


def test_an_old_submit_button_does_not_send(env):
    agent, tg, forms, job = make_agent(env, BASIC)
    agent.advance()
    agent.on_button({"data": f"s:{job['id']}:{job['ver']}", "callback_id": "cb"})
    assert forms.submitted == 1
    assert tg.answers == ["The agent applies on its own. Nothing is waiting on you."]
