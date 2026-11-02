"""Employee reviews: public write-ups when they exist, and no invented rating when they do not."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import company_reviews  # noqa: E402
import interview_intel  # noqa: E402

TODAY = date(2026, 11, 1)


def hit(**kwargs):
    base = dict(
        title="Working at Acme",
        url="https://news.ycombinator.com/item?id=1",
        snippet="I loved working at Acme, but the hours were long and my manager was kind.",
        source="Hacker News",
        when=date(2025, 6, 1),
    )
    base.update(kwargs)
    return interview_intel.Hit(**base)


def research(company="Acme", hits=None, search=None, **kwargs):
    if search is None:
        found = hits if hits is not None else [hit()]

        def search(_query):
            return found, []

    kwargs.setdefault("today", TODAY)
    return company_reviews.research(company, search=search, **kwargs)


class ReviewTests(unittest.TestCase):
    def test_a_review_is_quoted_and_themed(self):
        report = research()
        self.assertTrue(report.sourced)
        self.assertIn("loved working at Acme", report.markdown)
        self.assertIn("One review mentions hours or work-life", report.markdown)
        self.assertIn("One review mentions management", report.markdown)
        self.assertIn("One review mentions culture", report.markdown)
        self.assertIn("one person's account", report.markdown)
        self.assertNotIn("AI estimate", report.markdown)

    def test_a_credential_and_a_product_blurb_are_dropped(self):
        found = [
            hit(title="My background", url="https://news.ycombinator.com/item?id=2",
                snippet="I worked at Acme for two years before consulting."),
            hit(title="Acme API", url="https://example.com/api",
                snippet="Acme's API pricing is great for customers."),
        ]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertIn("no public employee reviews found", report.markdown)
        self.assertIn("does not add a rating", report.markdown)

    def test_glassdoor_is_not_fetched(self):
        opened = []
        found = [hit(
            title="Acme reviews",
            url="https://www.glassdoor.com/Reviews/Acme.htm",
            snippet="Work-life balance at Acme is poor and manager turnover is high.",
            source="DuckDuckGo",
        )]
        report = research(hits=found, open_page=lambda url: opened.append(url) or ("should not be read", None))
        self.assertEqual(opened, [])
        self.assertIn("this site is not opened", report.markdown)
        self.assertIn("Work-life balance at Acme is poor", report.markdown)

    def test_hiring_posts_and_other_companies_are_dropped(self):
        found = [
            hit(title="Ask HN: Who is hiring?", url="https://news.ycombinator.com/item?id=3",
                snippet="Acme | remote | great culture and good manager."),
            hit(title="Join us", url="https://example.com/jobs",
                snippet="We are hiring. The culture at Acme is great."),
            hit(title="A blog", url="https://news.ycombinator.com/item?id=4",
                snippet="I liked my manager at Initech. Acme was also on my list."),
            hit(title="Question", url="https://news.ycombinator.com/item?id=5",
                snippet="Does anyone know the culture at Acme?"),
        ]
        report = research(hits=found)
        self.assertFalse(report.sourced)

    def test_a_thread_title_does_not_invent_a_theme(self):
        found = [hit(
            title="Acme layoffs",
            snippet="The culture at Acme is collaborative and my hours were reasonable.",
        )]
        report = research(hits=found)
        self.assertIn("One review mentions culture", report.markdown)
        self.assertNotIn("One review mentions layoffs", report.markdown)

    def test_an_old_review_is_flagged(self):
        report = research(hits=[hit(when=date(2018, 1, 1))])
        self.assertIn("2018", report.markdown)
        self.assertIn("older than three years", report.markdown)

    def test_reading_about_layoffs_is_not_a_review(self):
        found = [hit(
            title="Notes",
            url="https://news.ycombinator.com/item?id=14",
            snippet="I have been reading about the Acme layoffs. Most of this is unrelated.",
        )]
        self.assertFalse(research(hits=found).sourced)

    def test_a_layoff_letter_comparison_is_not_a_review(self):
        found = [hit(
            title="Other Co lays off 11,000 people",
            url="https://news.ycombinator.com/item?id=13",
            snippet="Compare the Acme layoff to the other one. The letters were similar, including email access for 24 hours.",
        )]
        report = research(hits=found)
        self.assertFalse(report.sourced)
        self.assertNotIn("hours or work-life", report.markdown)

    def test_a_common_word_is_not_the_company(self):
        found = [
            hit(title="Layoff process", url="https://news.ycombinator.com/item?id=20",
                snippet="I've worked at big employers where the layoff target was set, if not absolute size."),
            hit(title="Affirm the point", url="https://news.ycombinator.com/item?id=21",
                snippet="As someone who worked at a consulting shop, I can affirm that underpaid contractors exist."),
            hit(title="The bill", url="https://news.ycombinator.com/item?id=22",
                snippet="No one person gets fired but there might be a round of layoffs to pay the bill."),
        ]
        self.assertFalse(research("absolute", hits=found[:1]).sourced)
        self.assertFalse(research("Affirm", hits=found[1:2]).sourced)
        self.assertFalse(research("BILL", hits=found[2:]).sourced)
        real = hit(snippet="I worked at BILL. I was caught up in the layoffs and would not recommend it.")
        self.assertTrue(research("BILL", hits=[real]).sourced)

    def test_a_joke_about_working_there_is_not_a_review(self):
        found = [hit(
            title="Android Pay",
            url="https://news.ycombinator.com/item?id=12",
            snippet="But what if I worked at Acme and only wanted to type my username.",
        )]
        self.assertFalse(research(hits=found).sourced)

    def test_a_worked_at_line_keeps_the_next_judgment(self):
        found = [hit(snippet="I worked at Acme too. The design system at the time was exemplary.")]
        report = research(hits=found)
        self.assertIn("exemplary", report.markdown)
        self.assertIn("I worked at Acme too", report.markdown)
        found = [hit(snippet="I loved working at Acme. The hours were long after that.")]
        report = research(hits=found)
        self.assertIn("hours were long after that", report.markdown)

    def test_a_blocked_search_does_not_invent_a_score(self):
        def search(_query):
            return [], ["DuckDuckGo: bot check"]

        report = research(hits=[], search=search)
        self.assertFalse(report.sourced)
        self.assertIn("DuckDuckGo: bot check", report.markdown)
        self.assertIn("not a score", report.markdown)
        self.assertNotIn("employees love", report.markdown)
        self.assertNotIn("/5", report.markdown)

    def test_two_reviews_are_counted(self):
        found = [
            hit(),
            hit(title="Another year at Acme", url="https://news.ycombinator.com/item?id=9",
                snippet="Pay at Acme was low and the layoffs were sudden."),
        ]
        report = research(hits=found)
        self.assertIn("**Evidence:** 2 public employee reviews", report.markdown)
        self.assertIn("One review mentions pay", report.markdown)
        self.assertIn("One review mentions layoffs or job security", report.markdown)


class CliTests(unittest.TestCase):
    def test_main_writes_the_file(self):
        def fake(company, **_kwargs):
            return interview_intel.Report(company, "", [], [], [], False, "# Company reviews: Acme\n\nno public employee reviews found\n")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "company_reviews.md"
            with mock.patch.object(company_reviews, "research", fake):
                code = company_reviews.main(["Acme", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("no public employee reviews found", out.read_text(encoding="utf-8"))

    def test_blank_company_is_an_error(self):
        self.assertEqual(company_reviews.main(["   "]), 2)


if __name__ == "__main__":
    unittest.main()
