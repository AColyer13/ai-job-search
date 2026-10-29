"""The facts about Adam live in several files. These tests fail when one of them disagrees with the oracle
(research/master_profile.json). Personal files are gitignored, so a check skips when its file is absent."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "job_scraper"))
import apply_answers  # noqa: E402
import oracle  # noqa: E402

ORACLE = oracle.load()
pytestmark = pytest.mark.skipif(ORACLE is None, reason="research/master_profile.json is not on this machine")

SKILL_PROFILE = ROOT / ".claude" / "skills" / "job-application-assistant" / "01-candidate-profile.md"


def read(path):
    if not path.exists():
        pytest.skip(f"{path.name} is not on this machine")
    return path.read_text(encoding="utf-8")


def role_line(text, role):
    """The first line of `text` that names both the role's employer and its title."""
    for line in text.splitlines():
        if role["employer"] in line and role["title"] in line:
            return line
    return ""


@pytest.mark.parametrize("path", [ROOT / "CLAUDE.md", SKILL_PROFILE, ROOT / "documents" / "cv" / "resume.md"],
                         ids=lambda p: p.name)
def test_mercor_start_date_matches_oracle(path):
    mercor = next(r for r in ORACLE["roles"] if r["employer"] == "Mercor")
    assert mercor["start"] in role_line(read(path), mercor)


def test_oracle_validates_and_rejects_a_broken_one():
    with pytest.raises(oracle.OracleError):
        oracle.validate({"identity": {}})
    with pytest.raises(oracle.OracleError):
        oracle.validate({**ORACLE, "banned_claims": [{"pattern": "("}]})


def test_resume_lists_every_oracle_role_with_its_dates():
    resume = read(ROOT / "documents" / "cv" / "resume.md")
    for r in ORACLE["roles"]:
        line = role_line(resume, r)
        assert r["title"] in line and r["start"] in line and r["end"] in line, r["employer"]


def test_resume_contains_every_oracle_metric():
    resume = read(ROOT / "documents" / "cv" / "resume.md")
    for metric in ORACLE["metrics"]:
        assert metric in resume, metric


def test_apply_profile_matches_oracle_identity():
    ident = ORACLE["identity"]
    defaults = apply_answers.DEFAULT_PROFILE
    for ours, theirs in (("email", "email"), ("phone", "phone"), ("linkedin", "linkedin"), ("github", "github"),
                         ("portfolio", "portfolio"), ("location", "location"), ("full_name", "full_name")):
        assert defaults[ours] == ident[theirs], ours
    saved = ROOT / "daily" / "apply_profile.json"
    if saved.exists():
        profile = json.loads(saved.read_text(encoding="utf-8"))
        for key in ("email", "phone", "linkedin", "github", "portfolio", "location", "full_name"):
            assert profile[key] == ident[key], key


@pytest.mark.parametrize("path", [ROOT / "documents" / "cv" / "resume.md", ROOT / "documents" / "cv" / "application_facts.md"],
                         ids=lambda p: p.name)
def test_no_banned_claim_in_text_sent_to_employers(path):
    text = read(path)
    # Rule text that says "never claim X" legitimately names X, so only lines that read as claims about Adam count.
    for claim in ORACLE["banned_claims"]:
        for line in text.splitlines():
            if re.search(claim["pattern"], line, re.I) and not re.search(r"\b(never|do not|don't|not)\b", line, re.I):
                pytest.fail(f"{path.name}: {claim['why']}: {line.strip()[:120]}")
