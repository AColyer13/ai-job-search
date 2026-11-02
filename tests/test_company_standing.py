"""Company standing is stored evidence. Unknown does not move a grade."""
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))

import company_standing  # noqa: E402
import scoring  # noqa: E402

BODY = (
    "We build cloud software for customers. " * 12
    + "You will run product demos, answer technical questions, and work with sales. "
    + "Two to three years of customer-facing experience. Bachelor's degree or equivalent experience."
)
OPEN = (
    "We build cloud software for customers. " * 12
    + "Career changers are welcome. You will write product code with the team. "
    + "A bachelor's degree or equivalent experience is fine."
)


def _review(url, detail, when=None, stale=False):
    return SimpleNamespace(
        url=url, detail=detail, note="", source="Example",
        when=when or date(2026, 1, 15), stale=stale, title="t",
    )


def _stood(standing, records=None, stale=0, name="Acme Software"):
    return {"standing": standing, "records": records or [], "stale": stale, "name": name}


def _pages(stance, *urls):
    return [
        {"url": url, "stance": stance, "date": "2026-01-15", "source": "Example", "note": stance}
        for url in urls
    ]


class Judge(unittest.TestCase):
    def test_two_positive_pages_are_outstanding_and_one_is_not(self):
        one = _pages("positive", "https://example.com/a")
        two = _pages("positive", "https://example.com/a", "https://example.com/b")
        self.assertEqual(company_standing.judge(one, date(2026, 6, 1)), "thin")
        self.assertEqual(company_standing.judge(two, date(2026, 6, 1)), "outstanding")

    def test_two_negative_pages_are_bad_and_disagreement_is_mixed(self):
        bad = _pages("negative", "https://example.com/a", "https://example.com/b")
        mixed = _pages("positive", "https://example.com/a") + _pages("negative", "https://example.com/b")
        self.assertEqual(company_standing.judge(bad, date(2026, 6, 1)), "bad")
        self.assertEqual(company_standing.judge(mixed, date(2026, 6, 1)), "mixed")

    def test_one_page_that_says_both_does_not_cap_the_company(self):
        both = [{"url": "https://example.com/a", "stance": "mixed", "date": "2026-01-15"}]
        self.assertEqual(company_standing.judge(both, date(2026, 6, 1)), "thin")

    def test_old_writeups_do_not_count_and_a_fake_record_does(self):
        old = _pages("negative", "https://example.com/a", "https://example.com/b")
        for record in old:
            record["date"] = "2019-01-01"
        self.assertEqual(company_standing.judge(old, date(2026, 6, 1)), "unknown")
        old.append({"url": "https://example.com/scam", "stance": "fake", "date": "2019-01-01"})
        self.assertEqual(company_standing.judge(old, date(2026, 6, 1)), "fake")

    def test_same_url_is_one_page(self):
        dupes = _pages("positive", "https://example.com/a", "https://example.com/a")
        self.assertEqual(company_standing.judge(dupes, date(2026, 6, 1)), "thin")

    def test_legal_suffix_matches(self):
        self.assertEqual(company_standing.key_of("Stripe, Inc."), company_standing.key_of("Stripe"))
        self.assertNotEqual(company_standing.key_of("Meta"), company_standing.key_of("Metal"))

    def test_telegram_marks(self):
        self.assertEqual(company_standing.telegram_mark({"standing": "outstanding"}), ("✅", "good"))
        self.assertEqual(
            company_standing.telegram_mark({"standing": "bad"}),
            ("❌", "not a company you want to work for"),
        )
        self.assertEqual(company_standing.telegram_mark({"standing": "fake"})[0], "❌")
        for kind in ("unknown", "thin", "mixed"):
            self.assertEqual(company_standing.telegram_mark({"standing": kind}), ("○", "undecided"), kind)

    def test_a_layoff_list_matches_the_exact_company_and_does_not_change_the_mark(self):
        import tempfile
        notices = [
            {"company": "Affirm", "url": "https://example.com/affirm", "date": "2024-02-21",
             "note": "Public layoff notice: 60 jobs.", "source": "layoffs.fyi"},
            {"company": "Epic Games", "url": "https://example.com/epic-games", "date": "2024-01-01",
             "note": "Public layoff notice.", "source": "layoffs.fyi"},
            {"company": "Epic", "url": "not a url", "date": "2024-01-01", "note": "no link", "source": "layoffs.fyi"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"
            matched = company_standing.store_notices(["Affirm", "Epic"], notices, path=path, today=date(2026, 11, 1))
            self.assertEqual([name for name, _ in matched], ["Affirm"])
            stood = company_standing.lookup("Affirm", path=path, today=date(2026, 11, 1))
            self.assertEqual(stood["standing"], "unknown")
            self.assertEqual(company_standing.telegram_mark(stood), ("○", "undecided"))
            self.assertIn("does not change the mark", company_standing._list_detail(stood))
            self.assertEqual(company_standing.lookup("Epic", path=path)["records"], [])

    def test_ensure_searches_a_new_company_once(self):
        import tempfile

        calls = []

        def research(company):
            calls.append(company)
            return SimpleNamespace(findings=[])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"
            first = company_standing.ensure("New Co", path=path, research=research)
            second = company_standing.ensure("New Co Inc", path=path, research=research)
        self.assertEqual(calls, ["New Co"])
        self.assertEqual(first["standing"], "unknown")
        self.assertEqual(second["standing"], "unknown")
        self.assertIn("○ <b>undecided</b>", company_standing.mark_line("New Co", path=path))

    def test_laid_off_is_a_negative_judgment(self):
        self.assertEqual(
            company_standing.stance_of("I worked at Salesforce at the time and was laid off by reading the news."),
            "negative",
        )

    def test_fee_posting_is_fake_and_product_copy_is_not(self):
        self.assertTrue(company_standing.fake_posting("Applicants must pay a training fee before day one."))
        self.assertFalse(company_standing.fake_posting("You will build the gift card checkout and processing fees."))


class Store(unittest.TestCase):
    def test_missing_file_is_unknown(self):
        stood = company_standing.lookup("Acme Software", path=Path("daily") / "does-not-exist-standing.json")
        self.assertEqual(stood["standing"], "unknown")
        self.assertEqual(stood["records"], [])

    def test_refresh_and_note(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"

            def research(company):
                return SimpleNamespace(findings=[
                    _review("https://example.com/good", "I loved working at Acme. I would recommend it."),
                    _review("https://example.com/plain", "Acme sells software to businesses."),
                    _review("https://example.com/old", "I hated working at Acme.", when=date(2018, 1, 1), stale=True),
                ])

            stood = company_standing.refresh("Acme Software", path=path, research=research, today=date(2026, 6, 1))
            self.assertEqual(stood["added"], 2)
            self.assertEqual(stood["standing"], "thin")
            again = company_standing.refresh("Acme, Inc.", path=path, research=research, today=date(2026, 6, 2))
            self.assertEqual(len(company_standing.load(path)["companies"]["acme software"]["records"]), 2)
            self.assertEqual(again["standing"], "thin")
            noted = company_standing.add_note(
                "Acme Software", "positive", "https://example.com/second",
                "A second person would recommend Acme.", path=path, when=date(2026, 6, 3),
            )
            self.assertEqual(noted["standing"], "outstanding")
            with self.assertRaises(ValueError):
                company_standing.add_note("Acme Software", "fake", "not a url", "scam", path=path)

    def test_list_is_best_to_worst(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"
            today = date(2026, 6, 1)
            company_standing.add_note("Scam Co", "fake", "https://example.com/scam", "fee", path=path, when=today)
            company_standing.add_note("Bad Co", "negative", "https://example.com/b1", "toxic", path=path, when=today)
            company_standing.add_note("Bad Co", "negative", "https://example.com/b2", "would not recommend", path=path, when=today)
            company_standing.add_note("Sour Co", "negative", "https://example.com/s1", "toxic", path=path, when=today)
            company_standing.add_note("Split Co", "positive", "https://example.com/p1", "would recommend", path=path, when=today)
            company_standing.add_note("Split Co", "negative", "https://example.com/n1", "toxic", path=path, when=today)
            company_standing.refresh("Blank Co", path=path, research=lambda company: SimpleNamespace(findings=[]), today=today)
            company_standing.add_note("Okay Co", "positive", "https://example.com/ok", "would recommend", path=path, when=today)
            company_standing.add_note("Good Co", "positive", "https://example.com/g1", "would recommend", path=path, when=today)
            company_standing.add_note("Good Co", "positive", "https://example.com/g2", "great place to work", path=path, when=today)
            names = [name for name, _ in company_standing.ranked(path, today=today)]
            self.assertEqual(
                names,
                ["Good Co", "Okay Co", "Blank Co", "Split Co", "Sour Co", "Bad Co", "Scam Co"],
            )
            text = company_standing.render_list(path, today=today)
            self.assertLess(text.index("Good Co"), text.index("Scam Co"))
            self.assertIn("best to worst", text)

    def test_cli_show_and_blank_company(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"
            self.assertEqual(company_standing.main(["show", "Nope", "--path", str(path)]), 0)
            self.assertEqual(company_standing.main(["note", "Nope", "--stance", "fake", "--url", "nope", "--path", str(path)]), 2)
            self.assertEqual(
                company_standing.main([
                    "note", "Nope Co", "--stance", "fake", "--url", "https://example.com/scam",
                    "--note", "The posting asks applicants to pay.", "--path", str(path),
                ]),
                0,
            )
            stood = company_standing.lookup("Nope", path=path)
            self.assertEqual(stood["standing"], "fake")


class GradeImpact(unittest.TestCase):
    def _score(self, stood, **job):
        job.setdefault("title", "Solutions Engineer")
        job.setdefault("company", "Acme Software")
        job.setdefault("location", "Remote (United States)")
        job.setdefault("description", BODY)
        job.setdefault("body_status", "opened")
        with patch.object(company_standing, "lookup", lambda company, path=None, today=None: stood):
            return scoring.score(job)

    def test_unknown_matches_a_company_with_no_record(self):
        unknown = self._score(_stood("unknown"))
        base = self._score(_stood("unknown"))
        self.assertEqual(unknown["grade"], "high")
        self.assertEqual(unknown["score"], base["score"])
        self.assertEqual(unknown["company_standing"], "unknown")

    def test_bad_company_drops_a_high_role_off_the_list(self):
        base = self._score(_stood("unknown"))
        self.assertEqual(base["grade"], "high")
        self.assertGreaterEqual(base["score"], 70)
        bad = self._score(_stood("bad", _pages("negative", "https://example.com/a", "https://example.com/b")))
        self.assertEqual(bad["grade"], "low")
        self.assertLessEqual(bad["score"], company_standing.BAD_CAP)
        self.assertTrue(any("stays off the list" in line for line in bad["blockers"]))

    def test_mixed_company_cannot_be_high(self):
        mixed = self._score(_stood("mixed", _pages("positive", "https://example.com/a") + _pages("negative", "https://example.com/b")))
        self.assertEqual(mixed["grade"], "medium")
        self.assertLessEqual(mixed["score"], company_standing.MIXED_CAP)
        self.assertGreaterEqual(mixed["score"], 50)

    def test_one_negative_page_does_not_change_the_score(self):
        base = self._score(_stood("unknown"))
        thin = self._score(_stood("thin", _pages("negative", "https://example.com/a")))
        self.assertEqual(thin["grade"], base["grade"])
        self.assertEqual(thin["score"], base["score"])
        self.assertTrue(any("One person is not enough" in line for line in thin["blockers"]))

    def test_outstanding_lifts_a_fitting_medium_role_and_not_a_capped_one(self):
        plain = self._score(_stood("unknown"), title="Software Engineer", description=OPEN)
        self.assertEqual(plain["grade"], "medium", plain)
        self.assertLess(plain["score"], 70)
        lifted = self._score(
            _stood("outstanding", _pages("positive", "https://example.com/a", "https://example.com/b")),
            title="Software Engineer", description=OPEN,
        )
        self.assertEqual(lifted["score"], plain["score"] + company_standing.OUTSTANDING_BONUS)
        self.assertEqual(lifted["grade"], "high")
        self.assertTrue(any("adds 12 points" in line for line in lifted["reasons"]))

        stretch = BODY + " 3+ years of sales engineering experience is required."
        held = self._score(_stood("unknown"), description=stretch)
        boosted = self._score(
            _stood("outstanding", _pages("positive", "https://example.com/a", "https://example.com/b")),
            description=stretch,
        )
        self.assertNotEqual(held["grade"], "high")
        self.assertNotEqual(boosted["grade"], "high")
        self.assertLessEqual(boosted["score"], scoring.CAP_UNPROVEN)

        adjacent = self._score(
            _stood("outstanding", _pages("positive", "https://example.com/a", "https://example.com/b")),
            title="Technical Account Manager",
        )
        self.assertEqual(adjacent["grade"], "medium")
        self.assertLessEqual(adjacent["score"], 59)

    def test_fake_employer_and_fee_posting_score_zero(self):
        stored = self._score(_stood("fake", [{"url": "https://example.com/scam", "stance": "fake", "note": "fee"}]))
        self.assertEqual(stored["grade"], "low")
        self.assertEqual(stored["score"], 0)
        self.assertTrue(stored["disqualified"])
        fee = self._score(_stood("unknown"), description=BODY + " Applicants must pay a training fee before day one.")
        self.assertEqual(fee["grade"], "low")
        self.assertEqual(fee["score"], 0)
        self.assertTrue(any("fake-job pattern" in line for line in fee["blockers"]))

    def test_unread_bad_company_is_not_listed(self):
        out = self._score(_stood("bad", _pages("negative", "https://example.com/a", "https://example.com/b")),
                          description="", body_status="unread")
        self.assertEqual(out["grade"], "low")
        self.assertLess(out["score"], 50)

    def test_senior_title_stays_low_at_an_outstanding_company(self):
        out = self._score(
            _stood("outstanding", _pages("positive", "https://example.com/a", "https://example.com/b")),
            title="Senior Solutions Engineer",
        )
        self.assertEqual(out["grade"], "low")
        self.assertEqual(out["score"], 0)


if __name__ == "__main__":
    unittest.main()
