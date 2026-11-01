"""End-of-day look at Telegram questions.

Reads the day's asked labels from daily/apply_issues.jsonl. A question the profile
already answers, worded a little differently, is saved in learned_answers.json so
the next form does not ask again. A question that would need a new fact is only
reported. Form-reader junk (section ids, Search, Select) is ignored.

    python job_scraper/question_review.py
    python job_scraper/question_review.py --day 2026-11-01
"""
import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import apply_answers
from apply_forms import Field

ROOT = Path(__file__).resolve().parents[1]
ISSUES_PATH = ROOT / "daily" / "apply_issues.jsonl"
REPORT_PATH = ROOT / "daily" / "question_review.json"
DONE_PATH = ROOT / "daily" / "question_review_done.txt"

# Questions the standing answers already cover. A Telegram line that is the same
# question in other words can reuse that answer. Nothing here invents a fact.
CANONICAL = (
    "Are you legally authorized to work in the United States?",
    "Will you now or in the future require sponsorship?",
    "Are you willing to relocate?",
    "Do you currently hold a security clearance?",
    "Are you eligible to obtain a security clearance?",
    "Have you ever been fired or terminated?",
    "May we contact your current employer?",
    "Do you own a car?",
    "Are you at least 18 years old?",
    "What is your date of birth?",
    "How did you hear about this job?",
    "Are you willing to work overtime?",
    "Do you have professional references?",
    "Have you been previously employed by Mercor?",
        "What is your Spanish proficiency?",
        "What country are you based in?",
)

JUNK = re.compile(
    r"^(search|select|select a state|male|female)$|section_\d+_question_\d+|^field-\d+$|^[a-z0-9_-]{8,}$|solve for x|\d\s*\+\s*x\s*=",
    re.I,
)
NEGATION = re.compile(r"\b(not|never|unable|unwilling|n't)\b", re.I)
CLOSE = 0.84


def _negated(text):
    return bool(NEGATION.search(text or ""))


def asked_labels(day, path=None):
    """Unique question texts asked on `day` (YYYY-MM-DD, local)."""
    wanted = str(day)
    seen, labels = set(), []
    try:
        lines = Path(path or ISSUES_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") != "asked":
            continue
        stamp = str(row.get("ts") or "")
        if not stamp.startswith(wanted):
            continue
        label = (row.get("label") or "").strip()
        key = apply_answers.normalize_label(label)
        if not key or key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _shapes(label):
    """The same question as a text box, a yes/no, and a checkbox. One of those is how the form asked it."""
    key = apply_answers.normalize_label(label)
    return (
        Field(kind="text", label=label, key=key, required=True, options=[]),
        Field(kind="radio", label=label, key=key, required=True, options=["Yes", "No"]),
        Field(kind="checkbox", label=label, key=key, required=True, options=[]),
    )


def _still_asking(label, profile):
    outcomes = [apply_answers.resolve(field, profile, {})[0] for field in _shapes(label)]
    return all(outcome == "ask" for outcome in outcomes)


def _catalog(profile):
    found = []
    for label in CANONICAL:
        kind = "text" if re.search(r"date of birth|proficiency|country", label, re.I) else "radio"
        options = [] if kind == "text" else ["Yes", "No"]
        if "proficiency" in label.lower():
            options = ["None", "Beginner", "Native"]
            kind = "select"
        if "fired" in label.lower():
            options = ["Yes", "No"]
            kind = "radio"
        field = Field(kind=kind, label=label, key=apply_answers.normalize_label(label), required=True, options=options)
        outcome, value, _source = apply_answers.resolve(field, profile, {})
        if outcome == "fill" and value not in (None, ""):
            found.append((apply_answers.normalize_label(label), label, str(value)))
    return found


def _closest(label, catalog):
    key = apply_answers.normalize_label(label)
    best, score = None, 0
    for norm, original, answer in catalog:
        if _negated(key) != _negated(norm):
            continue
        ratio = SequenceMatcher(None, key, norm).ratio()
        if ratio > score:
            best, score = (original, answer), ratio
    if best and score >= CLOSE:
        return best[0], best[1], score
    return None


def review(day=None, issues_path=None, learned_path=None, report_path=None, profile=None):
    """Save near-duplicate answers. Returns a short report dict."""
    day = day or date.today().isoformat()
    profile = profile if profile is not None else apply_answers.load_profile()
    learned_path = Path(learned_path or apply_answers.LEARNED_PATH)
    report_path = Path(report_path or REPORT_PATH)
    try:
        learned = json.loads(learned_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        learned = {"questions": []}
    known = {row.get("norm") for row in learned.get("questions") or []}
    catalog = _catalog(profile)
    added, already, needs, junk = [], [], [], []
    for label in asked_labels(day, issues_path):
        if JUNK.search(apply_answers.normalize_label(label)) or JUNK.search(label.strip()):
            junk.append(label)
            continue
        if not _still_asking(label, profile):
            already.append(label)
            continue
        hit = _closest(label, catalog)
        if hit and re.search(r"\b(reason|explain|describe|why)\b", label, re.I) and hit[1] in ("Yes", "No"):
            hit = None
        if hit is None:
            needs.append(label)
            continue
        original, answer, _score = hit
        norm = apply_answers.normalize_label(label)
        if norm in known:
            already.append(label)
            continue
        learned.setdefault("questions", []).append({
            "norm": norm, "label": label, "answer": answer,
            "same_as": original, "added": day,
        })
        known.add(norm)
        added.append(label)
    if added:
        learned_path.parent.mkdir(parents=True, exist_ok=True)
        learned_path.write_text(json.dumps(learned, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    report = {"day": day, "added": added, "already_answered": already, "needs_a_fact": needs, "ignored": junk}
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        prior = []
        if report_path.exists():
            prior = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                prior = [prior]
        prior = [row for row in prior if row.get("day") != day]
        prior.append(report)
        report_path.write_text(json.dumps(prior, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        pass
    return report


def summary(report):
    """One Telegram-sized note. Empty when there is nothing to say."""
    added, needs = report.get("added") or [], report.get("needs_a_fact") or []
    if not added and not needs:
        return ""
    lines = [f"End of day {report.get('day')}:"]
    if added:
        lines.append(f"Saved {len(added)} question{'s' if len(added) != 1 else ''} that match answers already on file.")
        lines += [f"• {item[:80]}" for item in added[:5]]
    if needs:
        lines.append(f"{len(needs)} still need a fact from you before they can be answered.")
    return "\n".join(lines)


def review_today(say=None):
    """Run once after active hours. Later calls the same day do nothing."""
    today = date.today().isoformat()
    try:
        if DONE_PATH.read_text(encoding="utf-8").strip() == today:
            return None
    except OSError:
        pass
    report = review(today)
    try:
        DONE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DONE_PATH.write_text(today + "\n", encoding="utf-8")
    except OSError:
        pass
    text = summary(report)
    if text and say:
        say(text)
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Review today's Telegram questions and save answers the profile already covers.")
    parser.add_argument("--day", default=date.today().isoformat())
    args = parser.parse_args()
    report = review(args.day)
    print(summary(report) or f"{args.day}: nothing new to save.")
    if report["needs_a_fact"]:
        print("Needs a fact:")
        for label in report["needs_a_fact"]:
            print(f"  - {label[:160]}")


if __name__ == "__main__":
    main()
