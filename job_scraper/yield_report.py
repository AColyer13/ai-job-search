"""Which applications get replies: job_search_tracker.csv grouped by what could have caused them.

    python job_scraper/yield_report.py [--today 2026-11-20] [--min-n 25]

Applications and applications-sent are costs, not results. The result is a positive reply (an interview,
assessment, offer), so every row below reads "positive / applications" for one group: where the job was
found, its fit score, the resume version, who submitted it, and the kind of role.

Two rules keep the numbers honest:

  * Only applications at least 21 days old count in a rate. A job applied to last week has not had time to
    answer, and counting it as a miss would punish whatever you did most recently. Newer applications are
    listed once, not graded.
  * A rate comes with a 95% interval (Wilson). With a handful of applications the interval spans most of 0 to
    100%, which is the point: the report says "thin" instead of letting a 0 out of 5 look like a finding.

Nothing here changes the pipeline. It prints; you decide. The closing line says whether there is enough data
to decide anything yet.
"""
import argparse
import csv
import io
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER_PATH = ROOT / "job_search_tracker.csv"

MATURE_DAYS = 21
MIN_APPLICATIONS_TO_DECIDE = 100
MIN_POSITIVES_TO_DECIDE = 10

POSITIVE = {"interview", "offer", "hired", "offer declined", "interview only"}
NEGATIVE = {"rejected"}
EXCLUDED = {"withdrawn"}  # not a result of the application itself


def _status(row):
    return (row.get("status") or "").strip().lower().replace("_", " ")


def wilson(positive, n, z=1.96):
    """95% interval for positive/n. (0, 0) for n == 0."""
    if n == 0:
        return 0.0, 0.0
    p = positive / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def score_bucket(row):
    raw = (row.get("score") or "").strip()
    if not raw:
        return "score unknown"
    score = int(float(raw))
    if score >= 70:
        return "70+ (High)"
    if score >= 50:
        return "50-69 (Medium)"
    if score >= 1:
        return "1-49 (Low)"
    return "0 (Low gate)"


DIMENSIONS = (
    ("Where the job was found", lambda r: (r.get("discovery_source") or "").strip() or "unknown"),
    ("Fit score at the time", score_bucket),
    ("Resume version", lambda r: (r.get("resume_version") or "").strip() or "unknown"),
    ("Who submitted it", lambda r: (r.get("apply_mode") or "").strip() or "unknown"),
    ("Kind of role", lambda r: (r.get("role_type") or "").strip() or "unknown"),
    ("Application form", lambda r: (r.get("channel") or "").strip() or "unknown"),
)


def load_rows(path=None):
    path = path or TRACKER_PATH
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))


def age_days(row, today):
    try:
        return (today - date.fromisoformat(row["date"])).days
    except (KeyError, ValueError):
        return None


def summarize(rows, today):
    """Split the tracker into the graded cohort and the rest. Returns (matured, too_new, undated)."""
    matured, too_new, undated = [], [], []
    for row in rows:
        if _status(row) in EXCLUDED:
            continue
        age = age_days(row, today)
        if age is None:
            undated.append(row)
        elif age >= MATURE_DAYS:
            matured.append(row)
        else:
            too_new.append(row)
    return matured, too_new, undated


def group_table(title, key, matured, min_n):
    groups = defaultdict(list)
    for row in matured:
        groups[key(row)].append(row)
    lines = [title, f"  {'group':<34}{'apps':>5}{'positive':>9}{'rejected':>9}{'rate (95% range)':>24}"]
    for name, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        n = len(members)
        positive = sum(_status(r) in POSITIVE for r in members)
        rejected = sum(_status(r) in NEGATIVE for r in members)
        low, high = wilson(positive, n)
        rate = f"{positive / n:>4.0%} ({low:.0%}-{high:.0%})"
        note = "  thin: read as noise" if n < min_n else ""
        lines.append(f"  {name[:33]:<34}{n:>5}{positive:>9}{rejected:>9}{rate:>24}{note}")
    return lines


def build_report(rows, today, min_n=25):
    matured, too_new, undated = summarize(rows, today)
    positives = sum(_status(r) in POSITIVE for r in matured)
    early = sum(_status(r) in POSITIVE for r in too_new)
    out = [
        f"Yield report, {today.isoformat()}",
        f"{len(rows)} tracked. Graded below: {len(matured)} at least {MATURE_DAYS} days old. "
        f"Not graded: {len(too_new)} newer ({early} of them already have a positive reply).",
    ]
    if undated:
        out.append(f"{len(undated)} rows have no readable date and are left out.")
    out.append("")
    if not matured:
        out.append("Nothing is old enough to grade yet.")
    for title, key in DIMENSIONS:
        if matured:
            out.extend(group_table(title, key, matured, min_n))
            out.append("")
    if len(matured) >= MIN_APPLICATIONS_TO_DECIDE and positives >= MIN_POSITIVES_TO_DECIDE:
        verdict = "Enough data to start acting on large gaps between groups. Check the ranges first."
    else:
        verdict = (
            f"Not enough to decide anything. Wait for {MIN_APPLICATIONS_TO_DECIDE}+ graded applications and "
            f"{MIN_POSITIVES_TO_DECIDE}+ positive replies (now {len(matured)} and {positives}). "
            "Do not drop a source, change the score cutoffs or pick a resume from these numbers."
        )
    out.append(verdict)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", help="ISO date to measure ages from, default today")
    ap.add_argument("--min-n", type=int, default=25, help="groups smaller than this are marked thin")
    ap.add_argument("--tracker", type=Path, help="tracker CSV, default job_search_tracker.csv")
    args = ap.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print("error: --today must be an ISO date like 2026-11-20", file=sys.stderr)
        return 1
    print(build_report(load_rows(args.tracker), today, args.min_n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
