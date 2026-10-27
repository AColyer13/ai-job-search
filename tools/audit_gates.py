"""Read-only tripwire reports for the shipped grader changes. Never saves the store.

    python tools/audit_gates.py himalayas     # C1: short Himalayas rows still unfilled
    python tools/audit_gates.py gates [DAYS]  # C3: rows the snippet and based-in gates changed
    python tools/audit_gates.py duplicates    # B1: every group the place/suffix merge joins

Each report prints the rows to read by hand. The kill criteria are in
job_scraper/EXECUTION_BRIEF.md; this file only shows the rows.
"""
import copy
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))

import daily_digest as dd  # noqa: E402
import pipeline  # noqa: E402
import scoring  # noqa: E402

SHORT = 500


def _rows():
    return list(dd.load_store().values())


def himalayas():
    rows = [j for j in _rows() if "himalayas.app" in (j.get("url") or "") and dd.omit_reason(j) is None
            and (j.get("fit") or "").lower() in ("high", "medium")]
    short = [j for j in rows if len(j.get("description") or "") < SHORT]
    print(f"accepted (high or medium) Himalayas rows: {len(rows)}; under {SHORT} characters: {len(short)}")
    by_day = {}
    for j in rows:
        day = by_day.setdefault(j.get("found_on") or "?", [0, 0])
        day[0] += 1
        day[1] += len(j.get("description") or "") < SHORT
    for day in sorted(by_day):
        total, small = by_day[day]
        print(f"  {day}: {small} of {total} short")
    for j in short:
        print("*", j.get("found_on"), "|", j.get("title"), "|", j.get("company"), "|", len(j.get("description") or ""))


def _regrade(job, gates_on):
    """Score a copy with the snippet-only gates switched off (gates_on=False) or as shipped."""
    saved = scoring.snippet_blocker
    if not gates_on:
        scoring.snippet_blocker = lambda company, text: None
    try:
        c = copy.deepcopy(job)
        c.pop("fit_before_location", None)
        return scoring.score(c)
    finally:
        scoring.snippet_blocker = saved


def gates(days=3):
    cutoff = (datetime.date.today() - datetime.timedelta(days=int(days))).isoformat()
    shown = 0
    for j in _rows():
        if (j.get("found_on") or "") < cutoff or dd.omit_reason(j) is not None:
            continue
        before, after = _regrade(j, False), _regrade(j, True)
        if before["grade"] != after["grade"]:
            shown += 1
            print("*", j.get("found_on"), "|", j.get("title"), "|", j.get("company"), "|", j.get("location"))
            print(f"    {before['grade']} -> {after['grade']}: {(after['blockers'] or [''])[-1]}")
            print("    text:", " ".join((j.get("description") or "").split())[:260])
    print(f"rows since {cutoff} the snippet gates moved: {shown}. Label each Fit, Stretch or Miss.")


def duplicates():
    rows = _rows()
    for j in rows:
        j["tier"] = pipeline.loc_tier(j.get("location"), j.get("work_mode"))
    kept, _ = pipeline.collapse_duplicates([copy.deepcopy(j) for j in rows])
    merged = [j for j in kept if j.get("also_places") or j.get("also_on")]
    print(f"rows {len(rows)} -> kept {len(kept)}")
    for j in merged:
        if j.get("also_places"):
            print("*", j.get("title"), "|", j.get("company"), "|", j.get("location"), "+", "; ".join(j["also_places"]))


def main(argv):
    if len(argv) < 2 or argv[1] not in ("himalayas", "gates", "duplicates"):
        print(__doc__)
        return 2
    {"himalayas": himalayas, "gates": gates, "duplicates": duplicates}[argv[1]](*argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
