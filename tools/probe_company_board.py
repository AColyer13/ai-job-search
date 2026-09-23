#!/usr/bin/env python3
"""Probe a Greenhouse/Lever/Ashby board slug before adding it to
job_scraper/queries.json's "company_boards" list.

Guessed slugs 404 far more often than they hit - this hits the real API once
and prints what it finds, so nothing gets added to queries.json on a guess.

Usage:
    python tools/probe_company_board.py greenhouse jamf
    python tools/probe_company_board.py lever clearcapital
    python tools/probe_company_board.py ashby <slug>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import pipeline  # noqa: E402


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in pipeline._BOARD_PLATFORMS:
        print(__doc__)
        return 1
    platform, slug = sys.argv[1], sys.argv[2]
    fn = pipeline._BOARD_PLATFORMS[platform]
    try:
        jobs = fn(slug)
    except Exception as e:
        print(f"FAIL {platform}/{slug}: {type(e).__name__}: {e}")
        print("Slug is likely wrong - do not add it to queries.json.")
        return 1
    print(f"OK {platform}/{slug}: {len(jobs)} open postings")
    for j in jobs[:5]:
        print(f"  - {j.get('title')} ({j.get('location')})")
    if jobs:
        print(f"\nSafe to add to queries.json's company_boards list as:")
        print(f'  {{ "name": "<Company Name>", "platform": "{platform}", "slug": "{slug}" }}')
    else:
        print("\n0 postings returned - board exists but is empty, or the slug is subtly wrong. Verify manually before adding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
