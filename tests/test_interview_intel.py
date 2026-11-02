"""Company interview reports: public write-ups when they exist, a labeled estimate when they do not."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import interview_intel  # noqa: E402

TODAY = date(2026, 11, 1)

LITE_HTML = """
<a class="result-link" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.reddit.com%2Fr%2Fcscareerquestions%2Fcomments%2Fabc%2Facme%2F&amp;rut=1">Acme interview experience</a>
<td class="result-snippet">Recruiter screen then a take-home at Acme.</td>
"""

HTML_ENDPOINT = """
<a class="result__a" href="https://example.com/acme-onsite">Acme onsite loop</a>
<div class="result__snippet">The onsite at Acme was a system design round.</div>
"""


def hit(**kwargs):
    base = dict(
        title="Acme interview experience",
        url="https://example.com/acme-interview",
        snippet="The Acme process was a recruiter screen and then a take-home.",
        source="Reddit r/cscareerquestions",
        when=date(2025, 6, 1),
    )
    base.update(kwargs)
    return interview_intel.Hit(**base)


def research(company="Acme", role="", hits=None, search=None, **kwargs):
    if search is None:
        found = hits if hits is not None else [hit()]

        def search(_query):
            return found, []

    kwargs.setdefault("today", TODAY)
    return interview_intel.research(company, role, search=search, **kwargs)


class ParseTests(unittest.TestCase):
    def test_lite_result_unwraps_the_redirect(self):
        found = interview_intel.parse_ddg(LITE_HTML)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].url, "https://www.reddit.com/r/cscareerquestions/comments/abc/acme/")
        self.assertIn("take-home", found[0].snippet)

    def test_html_endpoint_result(self):
        found = interview_intel.parse_ddg(HTML_ENDPOINT)
        self.assertEqual(found[0].url, "https://example.com/acme-onsite")
        self.assertIn("system design", found[0].snippet)

    def test_bot_check_is_an_error(self):
        def get(url, **_kwargs):
            return 200, "<html>anomaly detected prove you are human</html>", None

        found, err = interview_intel.search_ddg("Acme interview", get=get)
        self.assertEqual(found, [])
        self.assertEqual(err, "bot check")

    def test_a_challenge_page_with_status_202_is_a_bot_check(self):
        def get(url, **_kwargs):
            return 202, "<html>anomaly unfortunately</html>", None

        found, err = interview_intel.search_ddg("Acme interview", get=get)
        self.assertEqual(found, [])
        self.assertEqual(err, "bot check")

    def test_second_endpoint_is_used_when_the_first_page_has_no_results(self):
        def get(url, **_kwargs):
            if "lite.duckduckgo" in url:
                return 200, "<html><body>duckduckgo</body></html>", None
            return 200, HTML_ENDPOINT, None

        found, err = interview_intel.search_ddg("Acme interview", get=get)
        self.assertIsNone(err)
        self.assertEqual(found[0].url, "https://example.com/acme-onsite")

    def test_reddit_and_hn_parsers(self):
        reddit = interview_intel.parse_reddit({"data": {"children": [{"data": {
            "title": "Acme interview",
            "permalink": "/r/cscareerquestions/comments/abc/acme_interview/",
            "selftext": "Recruiter screen then a technical phone screen.",
            "subreddit": "cscareerquestions",
            "created_utc": 1717200000,
        }}]}})
        self.assertEqual(reddit[0].url, "https://www.reddit.com/r/cscareerquestions/comments/abc/acme_interview/")
        self.assertEqual(reddit[0].when, date(2024, 6, 1))

        hn = interview_intel.parse_hn({"hits": [{
            "story_title": "Acme interview",
            "comment_text": "The onsite at Acme had a system design round.",
            "objectID": "99",
            "created_at": "2025-01-02T00:00:00.000Z",
        }]})
        self.assertEqual(hn[0].url, "https://news.ycombinator.com/item?id=99")
        self.assertEqual(hn[0].when, date(2025, 1, 2))
        self.assertIn("system design", hn[0].snippet)


class ReportTests(unittest.TestCase):
    def test_reports_become_what_to_expect_and_skip_the_estimate(self):
        report = research(role="Engineer", open_page=lambda url: ("A recruiter screen, then a take-home.", None))
        self.assertTrue(report.sourced)
        self.assertIn("One report mentions a recruiter screen", report.markdown)
        self.assertIn("One report mentions a take-home exercise", report.markdown)
        self.assertIn("one person's experience", report.markdown)
        self.assertNotIn("AI estimate", report.markdown)

    def test_a_page_can_add_a_round_the_snippet_did_not_name(self):
        found = [hit(snippet="Read my Acme interview experience.")]
        report = research(hits=found, open_page=lambda url: ("The onsite at Acme had a system design round.", None))
        self.assertIn("system design round", report.markdown)
        self.assertIn("From the page.", report.markdown)

    def test_two_reports_are_counted(self):
        found = [
            hit(),
            hit(title="Another Acme interview", url="https://example.com/acme-2", source="Hacker News"),
        ]
        report = research(hits=found)
        self.assertIn("2 reports mention a recruiter screen", report.markdown)
        self.assertIn("**Evidence:** 2 public reports", report.markdown)

    def test_glassdoor_is_not_fetched(self):
        opened = []
        found = [hit(
            title="Acme interviews",
            url="https://www.glassdoor.com/Interview/Acme-interview.htm",
            snippet="Phone screen then an onsite interview at Acme.",
            source="DuckDuckGo",
        )]
        report = research(hits=found, open_page=lambda url: opened.append(url) or ("should not be read", None))
        self.assertEqual(opened, [])
        self.assertIn("this site is not opened", report.markdown)
        self.assertIn("One report mentions an onsite", report.markdown)

    def test_job_ads_generic_pages_and_private_urls_are_dropped(self):
        found = [
            hit(title="Acme is hiring", snippet="We are hiring. You will interview customers at Acme.", url="https://example.com/jobs"),
            hit(title="How to pass a coding interview", snippet="Practice every day.", url="https://example.com/generic"),
            hit(url="http://127.0.0.1/secret", snippet="Acme onsite interview was a system design round."),
            hit(title="Acme onsite", snippet="We are hiring. The Acme onsite is four rounds.", url="https://example.com/process"),
            hit(title="An Interview with the Acme CEO", snippet="We talked about the company's future.", url="https://example.com/press"),
        ]
        report = research(hits=found)
        self.assertTrue(report.sourced)
        self.assertIn("https://example.com/process", report.markdown)
        self.assertNotIn("interview customers", report.markdown)
        self.assertNotIn("127.0.0.1", report.markdown)
        self.assertNotIn("Practice every day", report.markdown)
        self.assertNotIn("example.com/press", report.markdown)

    def test_an_old_writeup_is_flagged(self):
        report = research(hits=[hit(when=date(2018, 1, 1))])
        self.assertIn("2018", report.markdown)
        self.assertIn("older than three years", report.markdown)
        self.assertIn("process may have changed", report.markdown)

    def test_company_wide_reports_say_they_do_not_name_the_role(self):
        report = research(role="Software Engineer")
        self.assertIn("None of these reports name the Software Engineer role", report.markdown)
        self.assertIn("Does not name this role.", report.markdown)

    def test_no_reports_is_honest_and_does_not_invent_rounds(self):
        def search(_query):
            return [], ["DuckDuckGo: bot check"]

        report = research(role="Engineer", hits=[], search=search)
        self.assertFalse(report.sourced)
        self.assertIn("**Evidence:** no public interview reports found", report.markdown)
        self.assertIn("DuckDuckGo: bot check", report.markdown)
        self.assertIn("No public interview write-up named Acme", report.markdown)
        self.assertIn(interview_intel.lead_sentence("Acme"), report.markdown)
        self.assertNotIn("One report mentions", report.markdown)
        self.assertIn("Do not assume a phone screen", report.markdown)

    def test_a_narrow_role_query_falls_back_to_the_company(self):
        calls = []

        def search(query):
            calls.append(query)
            if "Engineer" in query:
                return [], []
            return [hit()], []

        report = research(role="Engineer", search=search)
        self.assertEqual(len(calls), 2)
        self.assertTrue(report.sourced)
        self.assertEqual(len(calls), 2)

    def test_model_text_is_kept_only_when_it_stays_general(self):
        def ai(_prompt):
            return SimpleNamespace(ok=True, provider="grok", text=(
                "AI estimate. This is not a report of what Acme does.\n"
                "- Walk through a project you shipped.\n"
            ))

        report = research(hits=[], ai=ai)
        self.assertIn("Walk through a project you shipped.", report.markdown)
        self.assertIn("Written by grok.", report.markdown)

    def test_a_model_that_invents_a_company_process_is_discarded(self):
        def ai(_prompt):
            return SimpleNamespace(ok=True, provider="grok", text=(
                "AI estimate. This is not a report of what Acme does.\n"
                "Glassdoor says Acme asks two coding rounds.\n"
            ))

        report = research(hits=[], ai=ai)
        self.assertNotIn("Glassdoor says", report.markdown)
        self.assertNotIn("two coding rounds", report.markdown)
        self.assertIn("The model reply was not usable.", report.markdown)
        self.assertIn(interview_intel.lead_sentence("Acme"), report.markdown)

    def test_a_comment_keeps_the_process_sentence_only(self):
        junk = "A long argument about hiring trends in the industry. "
        body = junk + "&gt; The Acme onsite was a system design round."
        found = [hit(snippet=body, url="https://news.ycombinator.com/item?id=1", source="Hacker News")]
        opened = []
        report = research(hits=found, open_page=lambda url: opened.append(url))
        self.assertIn("system design round", report.markdown)
        self.assertNotIn("hiring trends", report.markdown)
        self.assertNotIn("&gt;", report.markdown)
        self.assertIn("From the comment.", report.markdown)
        self.assertEqual(opened, [])

    def test_a_thread_title_does_not_become_the_companys_process(self):
        found = [hit(
            title="Please stop the coding challenges",
            url="https://news.ycombinator.com/item?id=9",
            snippet="In general I was impressed with Acme's interview process.",
        )]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertNotIn("One report mentions a take-home", report.markdown)

    def test_detail_after_a_grounded_sentence_is_kept(self):
        found = [hit(
            title="Lessons from Acme",
            url="https://news.ycombinator.com/item?id=40",
            snippet="I recently had my onsite for a manager role at Acme. Acme interview process is lengthy. There's a mini onsite of three interviews, then a full day onsite.",
        )]
        report = research(hits=found)
        self.assertIn("mini onsite of three interviews", report.markdown)

    def test_another_companys_screens_are_not_counted(self):
        found = [hit(
            title="Guide to scaling engineering organizations",
            url="https://news.ycombinator.com/item?id=30",
            snippet="Acme was on my list. I passed all of my phone screens and got offers from most of my on-sites.",
        )]
        report = research(hits=found)
        self.assertFalse(report.sourced)

    def test_a_book_title_and_in_the_loop_are_not_rounds(self):
        found = [
            hit(title="How to get a job", url="https://news.ycombinator.com/item?id=21",
                snippet="Acme was one option. Currently going through Cracking the Coding Interview."),
            hit(title="Pay", url="https://news.ycombinator.com/item?id=22",
                snippet="Acme made an offer. Some of the other companies I'm in the loop with will pay more."),
        ]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertNotIn("One report mentions", report.markdown)

    def test_the_company_can_be_named_in_a_different_sentence(self):
        found = [hit(
            title="Acme interview",
            url="https://news.ycombinator.com/item?id=11",
            snippet="I had the onsite. It was three rounds.",
        )]
        report = research(hits=found)
        self.assertTrue(report.sourced)
        self.assertIn("I had the onsite", report.markdown)

    def test_a_who_is_hiring_post_is_not_an_interview_report(self):
        found = [hit(
            title="Ask HN: Who is hiring? (November 2016)",
            url="https://news.ycombinator.com/item?id=8",
            snippet="Acme | Seattle | Onsite Search Engineers",
        )]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertNotIn("One report mentions an onsite", report.markdown)

    def test_mentioning_an_interview_without_the_process_is_not_a_report(self):
        found = [hit(
            title="Ask HN: jobs",
            url="https://news.ycombinator.com/item?id=2",
            snippet="A friend interviewed at Acme in 2019 and then changed careers.",
        )]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertIn("no public interview reports found", report.markdown)

    def test_the_model_is_not_called_when_reports_exist(self):
        def ai(_prompt):
            raise AssertionError("model should not run")

        report = research(ai=ai)
        self.assertTrue(report.sourced)
        self.assertNotIn("AI estimate", report.markdown)


class CliTests(unittest.TestCase):
    def test_main_writes_the_file_without_calling_a_model(self):
        def fake(company, role="", ai=None, **_kwargs):
            self.assertIsNone(ai)
            return SimpleNamespace(markdown="# Interview intel: Acme\n\nNo public interview reports found\n")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "interview_intel.md"
            with mock.patch.object(interview_intel, "research", fake):
                with mock.patch.object(interview_intel, "load_ai", side_effect=AssertionError("model loaded")):
                    code = interview_intel.main(["Acme", "--role", "Engineer", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("No public interview reports found", out.read_text(encoding="utf-8"))

    def test_ai_flag_loads_the_model(self):
        seen = {}

        def fake(company, role="", ai=None, **_kwargs):
            seen["ai"] = ai
            return SimpleNamespace(markdown="ok\n")

        sentinel = object()
        with mock.patch.object(interview_intel, "research", fake):
            with mock.patch.object(interview_intel, "load_ai", return_value=sentinel):
                code = interview_intel.main(["Acme", "--ai"])
        self.assertEqual(code, 0)
        self.assertIs(seen["ai"], sentinel)

    def test_blank_company_is_an_error(self):
        code = interview_intel.main(["   "])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
