"""A drafted answer or cover letter may not claim anything the profile does not support."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import apply_answers  # noqa: E402
import claim_linter  # noqa: E402

SOURCE = """Sold the tip411 platform. Ran 100+ live product demos. Owned a $500K+ ServiceNow pipeline.
Built an ERP pipeline with $1.1M accepted by AEs as qualified. Sold technical products for nearly five years.
Scored Google Gemini search answers against Google quality rubrics."""


def problems(text):
    return claim_linter.check(text, SOURCE)


def test_a_draft_that_sticks_to_the_profile_passes():
    assert problems("I ran 100+ live product demos and owned a $500K+ pipeline over nearly five years.") == []


def test_unsupported_numbers_are_flagged():
    found = problems("I closed $2M in revenue and have 7 years of Python experience, up 40%.")
    assert any("$2M" in p for p in found) and any("7 years" in p for p in found) and any("40%" in p for p in found)


def test_banned_claims_are_flagged_even_when_the_numbers_are_fine():
    for text in ("I reviewed model-generated code for Google Gemini.", "I hit quota every quarter.",
                 "I practice TDD daily.", "I have 5+ years of sales experience.", "I am a staff engineer."):
        assert problems(text), text


def test_a_numeral_in_the_profile_is_not_flagged_by_formatting():
    assert problems("I ran 100+ demos.") == [] and problems("A $1.1M pipeline was accepted by AEs.") == []


class Runner:
    def __init__(self, text):
        self.text = text

    def __call__(self, *a, **k):
        return SimpleNamespace(ok=True, text=self.text, provider="claude", attempts=[])


def test_draft_with_an_unsupported_claim_is_refused_with_the_reason():
    text, why = apply_answers.draft("Why us?", {"title": "T", "company": "C"}, Runner("I have 5+ years of quota-carrying sales."))
    assert text is None and why.startswith("draft made claims the profile does not support")


def test_draft_that_passes_is_returned(monkeypatch):
    monkeypatch.setattr(apply_answers, "resume_text", lambda: SOURCE)
    monkeypatch.setattr(apply_answers, "_facts", lambda path=None: "")
    text, provider = apply_answers.draft("Why us?", {"title": "T", "company": "C"}, Runner("I ran 100+ live product demos."))
    assert text == "I ran 100+ live product demos." and provider == "claude"
