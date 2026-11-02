"""Real employer labels resolve known facts without guessing identity details."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "job_scraper"))
import apply_answers
import apply_forms


def resolve(label, options):
    field = apply_forms.Field(kind="combobox", label=label,
        key=apply_answers.normalize_label(label), required=True, options=options)
    return apply_answers.resolve(field, apply_answers.DEFAULT_PROFILE, {})


def test_combined_us_state_and_canadian_province_uses_residence():
    assert resolve("Which U.S. State or Canadian Province do you reside in?*",
                   ["Alabama", "Minnesota", "Ontario"])[:2] == ("fill", "Minnesota")


def test_pronouns_use_saved_demographic_decline_policy():
    assert resolve("Pronouns *", ["He/him/his", "They/them/theirs", "I prefer not to say"])[:2] == (
        "fill", "I prefer not to say")


def test_pronouns_without_decline_are_not_inferred():
    assert resolve("Pronouns *", ["He/him/his", "They/them/theirs"])[0] == "ask"


def test_education_start_year_does_not_get_employment_availability():
    field = apply_forms.Field(kind="number", label="Start date year*", key="start date year", required=True)
    assert apply_answers.resolve(field, apply_answers.DEFAULT_PROFILE, {})[0] == "ask"
