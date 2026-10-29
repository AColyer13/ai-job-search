"""A Greenhouse job embedded on an employer's own site (https://acme.com/careers?gh_jid=123) is still a Greenhouse form.

The link only becomes the hosted form after the public Greenhouse jobs API confirms the board and the job id.
Nothing here touches the network: the lookup is passed in.
"""
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import posting_text  # noqa: E402

from tests.test_apply_agent import BASIC, env, make_agent  # noqa: E402,F401  (env is a fixture)

EMBED = "https://expel.com/about/career-listing/8919466002?gh_jid=8919466002"
FORM = "https://job-boards.greenhouse.io/embed/job_app?for=expel&token=8919466002"


def board_api(*boards):
    """A lookup that answers like boards-api.greenhouse.io for the given (token, job id) pairs and 404s otherwise."""
    known = {f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}": jid for token, jid in boards}

    def fetch(url, accept="application/json"):
        if url in known:
            return json.dumps({"id": int(known[url]), "title": "Senior Technical Account Manager"})
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    return fetch


def test_embedded_link_becomes_the_hosted_form_after_the_api_confirms_it():
    fetch = board_api(("expel", "8919466002"))
    assert posting_text.greenhouse_form_url(EMBED, "Expel", fetch=fetch) == FORM


def test_the_board_name_can_come_from_the_site_domain_when_the_company_name_differs():
    fetch = board_api(("databricks", "7001"))
    assert posting_text.greenhouse_form_url("https://www.databricks.com/company/careers/x?gh_jid=7001", "Databricks, Inc.", fetch=fetch) \
        == "https://job-boards.greenhouse.io/embed/job_app?for=databricks&token=7001"


def test_no_answer_when_the_api_does_not_know_the_job():
    assert posting_text.greenhouse_form_url(EMBED, "Expel", fetch=board_api(("expel", "1"))) == ""
    assert posting_text.greenhouse_form_url(EMBED, "Expel", fetch=board_api()) == ""


def test_no_answer_when_the_api_returns_a_different_job_id():
    def fetch(url, accept="application/json"):
        return json.dumps({"id": 42})
    assert posting_text.greenhouse_form_url(EMBED, "Expel", fetch=fetch) == ""


def test_links_without_a_job_id_or_already_on_greenhouse_are_left_alone():
    fetch = board_api(("expel", "8919466002"))
    assert posting_text.greenhouse_form_url("https://expel.com/careers", "Expel", fetch=fetch) == ""
    assert posting_text.greenhouse_form_url("https://job-boards.greenhouse.io/expel/jobs/8919466002", "Expel", fetch=fetch) == ""


def test_queue_adds_an_embedded_greenhouse_job_as_its_hosted_form(env):
    agent, *_ = make_agent(env, BASIC)
    agent.jobs.clear()
    agent.embed_resolver = lambda url, company: FORM if "gh_jid" in url else ""
    added = agent.queue([{"url": EMBED, "title": "TAM", "company": "Expel", "fit": "high"}], limit=5, fits=("high",))
    assert [(j["url"], j["ats"]) for j in added] == [(FORM, "greenhouse")]


def test_queue_does_not_add_it_again_when_the_original_or_the_form_link_is_logged(env):
    agent, *_ = make_agent(env, BASIC)
    agent.embed_resolver = lambda url, company: FORM
    cand = [{"url": EMBED, "title": "TAM", "company": "Expel", "fit": "high"}]
    for logged in (EMBED, FORM):
        agent.jobs.clear()
        env.entries.clear()
        env.entries["k"] = {"url": logged}
        assert agent.queue(cand, limit=5, fits=("high",)) == [], logged


def test_queue_still_tries_an_embedded_link_that_cannot_be_resolved(env):
    agent, *_ = make_agent(env, BASIC)
    agent.jobs.clear()
    agent.embed_resolver = lambda url, company: ""
    assert agent.queue([{"url": EMBED, "title": "TAM", "company": "Expel", "fit": "high"}], limit=5, fits=("high",))
    (rec,) = agent.jobs.values()
    assert rec["status"] == "queued" and rec["url"] == EMBED  # the page itself will say whether a form is there
