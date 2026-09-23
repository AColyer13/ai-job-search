#!/usr/bin/env python3
"""Find tailored CV / cover letter pairs with no matching row in
job_search_tracker.csv.

`/apply` writes cv/main_<slug>.tex and cover_letters/cover_<slug>.tex.
`/outcome <company>` is what actually adds the tracker row and starts the
`documents/applications/<slug>/` archive - and it has to be run by hand after
submitting. It's easy for that second step to lag behind, and everything
downstream (`/rank`'s exclusion set, `/upskill`'s fit-weighted gap analysis,
`/html-report`, `/setup`'s outcome-calibration) reads the tracker as ground
truth, so a missed row means those tools are working from incomplete data
without any signal that they are.

This script never invents tracker data (no dates, no status, no fit rating -
none of that is safely inferable from a filename). It only reports the gap so
a person can close it with `/outcome <company> <role>`.

Usage:
    python tools/check_tracker_drift.py            # human-readable report
    python tools/check_tracker_drift.py --json      # machine-readable
Exit code is 1 if any drift is found, 0 otherwise (wire into CI or a hook if
you want this enforced automatically; it is advisory-only by default).
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CV_DIR = ROOT / "cv"
COVER_DIR = ROOT / "cover_letters"
TRACKER_PATH = ROOT / "job_search_tracker.csv"
ARCHIVE_DIR = ROOT / "documents" / "applications"


def _slug_from_filename(path, prefix):
    """cv/main_<slug>.tex -> <slug>; cover_letters/cover_<slug>.tex -> <slug>."""
    stem = path.stem
    if not stem.startswith(prefix):
        return None
    return stem[len(prefix):]


def find_application_slugs():
    """Every <company>_<role> slug that has at least one drafted file, keyed
    to which of (cv, cover_letter) exist for it."""
    slugs = {}
    if CV_DIR.exists():
        for f in CV_DIR.glob("main_*.tex"):
            if f.name == "main_example.tex":
                continue
            slug = _slug_from_filename(f, "main_")
            if slug:
                slugs.setdefault(slug, {"cv": False, "cover_letter": False})
                slugs[slug]["cv"] = True
    if COVER_DIR.exists():
        for f in COVER_DIR.glob("cover_*.tex"):
            if f.name == "cover_example.tex":
                continue
            slug = _slug_from_filename(f, "cover_")
            if slug:
                slugs.setdefault(slug, {"cv": False, "cover_letter": False})
                slugs[slug]["cover_letter"] = True
    return slugs


def _normalize(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_tracker_slugs():
    """Slugs the tracker already accounts for, derived from its cv_file /
    cover_letter_file columns (the authoritative link) and, as a fallback,
    from normalized company+role text (in case those columns are blank)."""
    if not TRACKER_PATH.exists():
        return set(), []
    rows = []
    file_slugs = set()
    text_slugs = set()
    with open(TRACKER_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
            for col in ("cv_file", "cover_letter_file"):
                v = (row.get(col) or "").strip()
                if not v:
                    continue
                stem = Path(v).stem
                for prefix in ("main_", "cover_"):
                    if stem.startswith(prefix):
                        file_slugs.add(stem[len(prefix):])
                        break
            company = _normalize(row.get("company"))
            role = _normalize(row.get("role"))
            if company and role:
                text_slugs.add(company + "_" + role)
                text_slugs.add(company)  # loose fallback: company alone
    return file_slugs, text_slugs, rows


def find_drift():
    app_slugs = find_application_slugs()
    file_slugs, text_slugs, rows = load_tracker_slugs()

    missing = []
    for slug, present in sorted(app_slugs.items()):
        if slug in file_slugs:
            continue
        norm_slug = _normalize(slug)
        if any(norm_slug.startswith(t) or t.startswith(norm_slug) for t in text_slugs if t):
            continue
        archive_exists = (ARCHIVE_DIR / slug).exists()
        missing.append({
            "slug": slug,
            "has_cv": present["cv"],
            "has_cover_letter": present["cover_letter"],
            "has_archive_folder": archive_exists,
        })
    return missing, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    missing, tracker_rows = find_drift()

    if args.json:
        print(json.dumps({"tracker_rows": tracker_rows, "missing": missing}, indent=2))
        return 1 if missing else 0

    if not missing:
        print(f"No drift: every drafted application has a job_search_tracker.csv row ({tracker_rows} rows checked).")
        return 0

    print(f"job_search_tracker.csv has {tracker_rows} row(s), but {len(missing)} drafted "
          f"application(s) have no matching row:\n")
    for m in missing:
        files = []
        if m["has_cv"]:
            files.append(f"cv/main_{m['slug']}.tex")
        if m["has_cover_letter"]:
            files.append(f"cover_letters/cover_{m['slug']}.tex")
        archive_note = " (documents/applications/ archive already exists)" if m["has_archive_folder"] else ""
        print(f"  - {m['slug']}{archive_note}")
        for fp in files:
            print(f"      {fp}")
    print(f"\nClose the gap with /outcome <company> <role> for each - it writes the tracker row\n"
          f"and starts the archive folder. This script never fills that in for you: dates,\n"
          f"status, and fit rating aren't recoverable from a filename.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
