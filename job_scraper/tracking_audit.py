"""Check that the apply agent's records agree with each other: the state file, the applied log and the tracker.

    python job_scraper/tracking_audit.py

Each check prints one line per problem and the exit code is 1 when anything is wrong, so a session (or the
listener, which also sends this on Telegram with /audit) can see a lost application before it matters.

  - an application the agent sent is missing from the applied log
  - an applied job is missing from job_search_tracker.csv (which /rank, /scrape and /gmail-sync read)
  - an applied job has no resume recorded
  - a Chrome window has been "assisting" far longer than the wait
  - a log write failed and is still waiting to be retried
  - a job was handed back after a closed window or a timeout: Adam may have pressed Submit, so the
    confirmation email is the proof (the 7:00 and 19:00 Gmail sync records it)
"""
import csv
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import applied_log  # noqa: E402
import daily_digest  # noqa: E402
import pipeline  # noqa: E402
from state_store import StateError, load_state  # noqa: E402

STATE_PATH = daily_digest.DAILY / "apply_state.json"
STUCK_SECONDS = 35 * 60  # the Chrome wait is 20 minutes plus the collector's 5 minute grace
UNSURE_REASONS = ("window was closed", "No confirmation within", "never saw a confirmation", "saw no confirmation")


def _tracker_rows(path=None):
    path = Path(path or applied_log.TRACKER_PATH)
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))


def audit(entries=None, jobs=None, tracker_rows=None, now=None):
    """A list of problem lines. Empty means every record agrees."""
    entries = applied_log.load_log() if entries is None else entries
    problems = []
    if jobs is None:
        try:
            jobs = load_state(STATE_PATH)
        except StateError as exc:
            problems.append(f"STATE UNREADABLE: {exc}")
            jobs = {}
    rows = _tracker_rows() if tracker_rows is None else tracker_rows
    now = time.time() if now is None else now

    logged = {daily_digest._key(e.get("url")): e for e in entries.values()}
    tracker_keys = {(pipeline.company_key(r.get("company")), pipeline._norm_words(r.get("role"))) for r in rows}
    tracker_urls = {r.get("source") for r in rows if r.get("source")}

    for job in jobs.values():
        name = f"{job.get('title') or 'Untitled'} at {job.get('company') or 'unknown'}"
        entry = logged.get(daily_digest._key(job.get("url")))
        if job.get("status") == "applied" and not job.get("note") == "applied elsewhere":
            if not entry or entry.get("status") != "applied":
                problems.append(f"SENT BUT NOT LOGGED: {name} is applied in the agent's state but not in the applied log")
        if job.get("unlogged"):
            problems.append(f"LOG WRITE PENDING: {name} ({job['unlogged'].get('status')}) is waiting to be written to the log")
        if job.get("status") == "assisting" and now - job.get("assist_started", now) > STUCK_SECONDS:
            problems.append(f"STUCK: {name} has been waiting on a Chrome window for over {STUCK_SECONDS // 60} minutes")
        reason = job.get("reason") or ""
        if job.get("status") == "handed_off" and any(s in reason for s in UNSURE_REASONS):
            problems.append(f"VERIFY BY EMAIL: {name} was handed back ({reason[:70]}). If you did press Submit, a confirmation email will log it")

    for entry in entries.values():
        if entry.get("status") != "applied":
            continue
        name = f"{entry.get('title') or 'Untitled'} at {entry.get('company') or 'unknown'}"
        key = (pipeline.company_key(entry.get("company")), pipeline._norm_words(entry.get("title")))
        if key not in tracker_keys and entry.get("url") not in tracker_urls:
            problems.append(f"NOT IN TRACKER: {name} is applied in the log but has no row in job_search_tracker.csv")
        if entry.get("applied_by") == "AI" and not entry.get("resume"):
            problems.append(f"NO RESUME RECORDED: {name} was sent by the AI but the log has no resume file")
    return problems


def main(argv=None):
    problems = audit()
    for line in problems:
        print(line)
    if not problems:
        print("Records agree: state, applied log and tracker.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
