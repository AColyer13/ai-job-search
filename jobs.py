"""Show the saved job shortlist without starting a search or applying."""
from datetime import datetime
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent / "job_scraper"))
import applied_log
import daily_digest


def posting_link(job):
    """Use a valid employer link, falling back to the original posting."""
    for value in (job.get("apply_url"), job.get("url")):
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
            continue
        try:
            parts = urlsplit(value)
            if (parts.scheme.lower() in ("http", "https") and parts.hostname
                    and parts.username is None and parts.password is None):
                parts.port  # reject malformed ports as well as malformed URLs
                return value
        except ValueError:
            continue
    return ""


def attempt_index(entries):
    """Match recorded attempts by either posting URL or the report's job identity."""
    urls, identities = set(), set()
    for entry in entries.values():
        if entry.get("url"):
            urls.add(daily_digest._key(entry["url"]))
        probe = {key: entry.get(key) for key in ("title", "company", "location", "work_mode")}
        identity = daily_digest.pipeline_dedupe_key(probe)
        if identity is not None:
            identities.add(identity)
    return urls, identities


def main():
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        store = daily_digest.load_store()
        entries = applied_log.load_log()
        cards = [{**job, "url": posting_link({"url": job.get("url")}),
                  "apply_url": posting_link({"url": job.get("apply_url")})} for job in store.values()]
        jobs = daily_digest.fit_jobs(cards)
        updated = datetime.fromtimestamp(daily_digest.STORE_PATH.stat().st_mtime) if jobs else None
        urls, identities = attempt_index(entries)
        eligible = []
        for job in jobs:
            link = posting_link(job)
            if not link:
                continue
            attempted = (any(daily_digest._key(job.get(key)) in urls for key in ("apply_url", "url")
                             if isinstance(job.get(key), str) and job[key])
                         or daily_digest.pipeline_dedupe_key(job) in identities)
            eligible.append((job, link, attempted))
        eligible.sort(key=lambda row: (row[2], daily_digest._rank_key(row[0])))
        displayed = [(job, link, attempted, daily_digest.result_mark({**job, "apply_url": link}, entries))
                     for job, link, attempted in eligible[:5]]
    except (OSError, ValueError, AttributeError, TypeError, applied_log.LogError):
        print("Cannot read the saved jobs or application log. Restore the damaged file before retrying.")
        return 1
    if not jobs:
        print("No matching jobs saved yet. The scheduled search will create your shortlist.")
        print("To search now, run: python job_scraper/daily_digest.py")
        return 0
    print(f"Saved shortlist: {len(jobs)} matching jobs | Updated {updated:%Y-%m-%d %H:%M}")
    missing = len(jobs) - len(eligible)
    if missing:
        print(f"Posting link unavailable for {missing} matching job(s); excluded from the shortlist.")
    if not eligible:
        print("No usable posting links are available. Check the full report or wait for the next scheduled search.")
        print(f"Full list: {daily_digest.DAILY / 'latest.docx'}")
        return 1
    print("Unattempted jobs first, then previously handled jobs; no search or application was started.\n")
    previous_group = None
    for index, (job, link, attempted, result) in enumerate(displayed, 1):
        if attempted != previous_group:
            print("Previously handled:" if attempted else "Unattempted jobs:")
            previous_group = attempted
        print(f"{index}. {job.get('title') or 'Untitled'} at {job.get('company') or 'Unknown company'}")
        print(f"   {job.get('fit', 'medium').title()} fit | {daily_digest._where(job)}")
        print(f"   {result[1] if result else 'Not attempted yet'}")
        print(f"   {link}\n")
    print(f"Full list: {daily_digest.DAILY / 'latest.docx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
